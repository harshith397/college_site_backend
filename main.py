from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from redis_utils import get_cached_data, set_cached_data, init_redis_connection, delete_cache
from services import fetch_dashboard_data
from tools import fetch_login_hidden_fields
from fastapi.middleware.cors import CORSMiddleware
import base64
from contextlib import asynccontextmanager
import httpx
import time
from typing import Optional
import asyncpg
import os

# ========================
# Environment Configuration
# ========================
DATABASE_URL = os.getenv("DATABASE_URL")  # Render PostgreSQL URL
REDIS_URL = os.getenv("REDIS_URL")  # Render Redis URL
ENVIRONMENT = os.getenv("ENVIRONMENT", "production")

# CORS origins - restrict in production
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")

# ========================
# Database Pool (Async)
# ========================
db_pool = None

async def init_db_pool():
    """Initialize asyncpg connection pool"""
    global db_pool
    db_pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=2,
        max_size=10,
        command_timeout=60
    )
    print("✅ Database pool created")

async def close_db_pool():
    """Close database pool"""
    global db_pool
    if db_pool:
        await db_pool.close()
        print("🛑 Database pool closed")

# ========================
# Lifespan Management
# ========================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_db_pool()
    app.state.redis = await init_redis_connection(REDIS_URL)
    print("✅ Redis connected on startup")
    
    yield  # Application runs here
    
    # Shutdown
    await app.state.redis.aclose()
    await close_db_pool()
    print("🛑 All connections closed")

# ========================
# FastAPI App
# ========================
app = FastAPI(
    title="College Site Proxy API",
    lifespan=lifespan,
    docs_url="/docs" if ENVIRONMENT == "development" else None,
    redoc_url=None
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========================
# Request/Response Models
# ========================
class LoginRequest(BaseModel):
    userid: str
    password: str
    captcha: str
    session_id: str

class DashboardResponse(BaseModel):
    dashboardData: dict

class DashboardRequest(BaseModel):
    session_id: str

class LoginResponse(BaseModel):
    success: bool
    detail: str = None
    dashboard: dict = None

class LogoutRequest(BaseModel):
    session_id: str

class LogoutResponse(BaseModel):
    success: bool
    message: str

# ========================
# Constants
# ========================
LOGIN_URL = "https://erp.vce.ac.in/sinfo/Default.aspx"
DASHBOARD_URL = "https://erp.vce.ac.in/sinfo/DashBoard.aspx"
CAPTCHA_URL = "https://erp.vce.ac.in/sinfo/CaptchaImage.aspx"

DEPT_SCHEMA_MAP = {
    "BE - Computer Science and Engineering (AIML)": "be_csm",
    "BE - Computer Science and Engineering": "be_cse",
    "BE - Information Technology": "be_it",
    "BE - Electronics and Communications Engineering": "be_ece",
    "BE - Electrical and Electronics Engineering": "be_eee",
    "BE - Civil Engineering": "be_civil",
    "BE - Mechanical Engineering": "be_mech"
}

# ========================
# Helper Functions
# ========================
def find_schema_for_dept(dept_param: str) -> Optional[str]:
    """Find schema code for provided dept string (case-insensitive, partial match fallback)."""
    if not dept_param:
        return None
    key = dept_param.strip().lower()
    
    # Exact key match
    for k, v in DEPT_SCHEMA_MAP.items():
        if k.strip().lower() == key:
            return v
    
    # Direct code match
    for v in DEPT_SCHEMA_MAP.values():
        if key == v.lower():
            return v
    
    # Substring match fallback
    for k, v in DEPT_SCHEMA_MAP.items():
        if key in k.strip().lower() or k.strip().lower() in key:
            return v
    
    return None

# ========================
# Routes
# ========================

@app.get("/")
async def root():
    """Health check endpoint"""
    return {"status": "ok", "message": "College API is running"}

@app.get("/health")
async def health():
    """Detailed health check"""
    try:
        # Check Redis
        await app.state.redis.ping()
        redis_status = "connected"
    except Exception as e:
        redis_status = f"error: {str(e)}"
    
    try:
        # Check Database
        async with db_pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"
    
    return {
        "status": "ok",
        "redis": redis_status,
        "database": db_status,
        "environment": ENVIRONMENT
    }

@app.post("/logout", response_model=LogoutResponse)
async def logout(req: LogoutRequest):
    """Logout user and clear session cache"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/140.0.0.0 Safari/537.36"
        ),
        "Referer": LOGIN_URL,
    }

    redis_key = f"erp:session:{req.session_id}"
    cached_data = await get_cached_data(app.state.redis, redis_key)

    if not cached_data:
        return {"success": False, "message": "No active session or already logged out"}

    hidden_fields = cached_data["hiddenFields"]
    payload = {
        "__VIEWSTATE": hidden_fields.get("viewstate"),
        "__VIEWSTATEGENERATOR": hidden_fields.get("viewstate_generator"),
        "__EVENTVALIDATION": hidden_fields.get("event_validation"),
        "btnLogOut": "Logout",
    }

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            DASHBOARD_URL,
            data=payload,
            headers=headers,
            cookies={"ASP.NET_SessionId": req.session_id},
        )

    await delete_cache(app.state.redis, redis_key)
    
    if "Please sign in" in response.text:
        return {"success": True, "message": "Logged out successfully"}
    
    return {"success": True, "message": "Logged out successfully"}

@app.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest):
    """Login user with credentials"""
    payload = await fetch_login_hidden_fields()  # ✅ Now async
    payload.update({
        "txt_HTNO": req.userid,
        "txt_Password": req.password,
        "btn_Login": "Sign in",
        "txtCaptcha": req.captcha
    })

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            LOGIN_URL,
            data=payload,
            cookies={"ASP.NET_SessionId": req.session_id}
        )
    
    html_text = response.text

    if "Invalid Captcha. Please try again." in html_text:
        return {"success": False, "detail": "captcha invalid"}
    elif "Ivalid Userid or Password" in html_text:
        return {"success": False, "detail": "credentials invalid"}
    else:
        return {"success": True, "detail": "Login Successful"}

@app.get("/captcha")
async def get_captcha():
    """Fetch captcha image and session ID"""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(CAPTCHA_URL)
            response.raise_for_status()

            captcha_base64 = "data:image/jpeg;base64," + base64.b64encode(response.content).decode()
            session_cookie = response.cookies.get("ASP.NET_SessionId")

            return JSONResponse({
                "captcha_image": captcha_base64,
                "session_id": session_cookie
            })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch captcha: {str(e)}")

@app.post("/dashboard", response_model=DashboardResponse)
async def dashboard(req: DashboardRequest):
    """Fetch user dashboard data with caching"""
    start_total = time.perf_counter()
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
        "Referer": LOGIN_URL,
        "Cookie": f"ASP.NET_SessionId={req.session_id}"
    }
    
    redis_key = f"erp:session:{req.session_id}"
    
    # Check cache
    start = time.perf_counter()
    cached_data = await get_cached_data(app.state.redis, redis_key)
    print(f"⏱️  Redis check: {(time.perf_counter() - start)*1000:.2f}ms")
    
    if cached_data:
        print(f"⏱️  TOTAL (cache hit): {(time.perf_counter() - start_total)*1000:.2f}ms")
        return {"dashboardData": cached_data["dashboardData"]}
    
    # Fetch from ERP
    start = time.perf_counter()
    async with httpx.AsyncClient(timeout=30.0) as client:
        dashboard_response = await client.get(DASHBOARD_URL, headers=headers)
    print(f"⏱️  Dashboard HTML fetch: {(time.perf_counter() - start)*1000:.2f}ms")
    
    html_text = dashboard_response.text

    if "Logout" not in html_text:
        raise HTTPException(status_code=401, detail="Session expired")

    dashboard_data = await fetch_dashboard_data(html_text, req.session_id)
    
    # Cache the data
    start = time.perf_counter()
    await set_cached_data(app.state.redis, redis_key, dashboard_data, ttl=3600)
    print(f"⏱️  Redis write: {(time.perf_counter() - start)*1000:.2f}ms")
    print(f"⏱️  TOTAL (cache miss): {(time.perf_counter() - start_total)*1000:.2f}ms")

    return {"dashboardData": dashboard_data["dashboardData"]}

@app.get("/get_syllabus")
async def get_syllabus(
    semester: int = Query(..., description="Semester number (1-8)"),
    subject_code: str = Query(..., description="Subject code, e.g., CS-I"),
    dept: str = Query(..., description="Department identifier")
):
    """Fetch syllabus data from database (async)"""
    # Validate semester
    if semester < 1 or semester > 8:
        raise HTTPException(status_code=400, detail="Semester must be between 1 and 8")

    # Resolve department schema
    if semester in [1, 2]:
        dept = "BE - Computer Science and Engineering"
    
    schema = find_schema_for_dept(dept)
    if not schema:
        raise HTTPException(status_code=400, detail=f"Unknown department '{dept}'")
    
    table_name = f"semester_{semester}"

    try:
        async with db_pool.acquire() as conn:
            # Set search path
            await conn.execute(f"SET search_path TO {schema}")
            
            # Query syllabus data
            query = f"SELECT syllabus_data FROM {table_name} WHERE subject_code = $1"
            result = await conn.fetchval(query, subject_code)

            if not result:
                raise HTTPException(status_code=404, detail="Subject not found")

            return result

    except asyncpg.exceptions.UndefinedTableError:
        raise HTTPException(status_code=404, detail="Semester table not found")
    except Exception as e:
        print(f"Database error: {str(e)}")
        raise HTTPException(status_code=500, detail="Database query failed")

# ========================
# Run with: uvicorn main:app --reload
# ========================