import requests
from tools import get_student_info,get_current_sem,get_attendance_subjects,parse_marks_table
from bs4 import BeautifulSoup
from pprint import pprint
from concurrent.futures import ThreadPoolExecutor,as_completed
import time
import httpx
import asyncio

LOGIN_URL = "https://erp.vce.ac.in/sinfo/Default.aspx"
DASHBOARD_URL = "https://erp.vce.ac.in/sinfo/DashBoard.aspx"

async def fetch_dashboard_data(html: str,session_id: str) -> dict:
    start_total = time.perf_counter()
    soup = BeautifulSoup(html, 'lxml')
    viewstate = soup.find("input", {"name": "__VIEWSTATE"})["value"]
    viewstate_generator = soup.find("input", {"name": "__VIEWSTATEGENERATOR"})["value"]
    event_validation = soup.find("input", {"name": "__EVENTVALIDATION"})["value"]


    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
        "Referer": LOGIN_URL
    }
    headers["Cookie"]=f"ASP.NET_SessionId={session_id}"
    start = time.perf_counter()
    student_dashboard = get_student_info(html)
    current_sem = get_current_sem(html)
    print(f"⏱️  Dashboard parsing: {(time.perf_counter() - start)*1000:.2f}ms")

    start = time.perf_counter()
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        start = time.perf_counter()
        
        attendance_task = client.get(current_sem['Attendance Link'], headers=headers)
        marks_task = client.get(current_sem["Marks Link"], headers=headers)
        
        # Wait for both to complete in parallel
        attendance_response, marks_response = await asyncio.gather(
            attendance_task,
            marks_task
        )
        
        print(f"⏱️  HTTP fetching (parallel): {(time.perf_counter() - start)*1000:.2f}ms")

    start = time.perf_counter()
    marks_data,gender = parse_marks_table(marks_response.text)
    sub_attendance_data,total_attendance_data,img_url=get_attendance_subjects(attendance_response.text)
    print(f"⏱️  Response parsing: {(time.perf_counter() - start)*1000:.2f}ms")
    
    #pprint(sub_attendance_data)
    #pprint(total_attendance_data)
    print(f"⏱️  TOTAL fetch_dashboard_data: {(time.perf_counter() - start_total)*1000:.2f}ms")
    student_dashboard["Gender"]=gender
    return {"dashboardData":{
        "DashBoard" : student_dashboard,
        "Current Sem" : current_sem,
        "Subjects Attendance Data" : sub_attendance_data,
        "Total Attendance Data" : total_attendance_data,
        "Student Image" : img_url,
        "Marks Data" : marks_data
    },
    "hiddenFields":{
        "viewstate": viewstate,
        "viewstate_generator": viewstate_generator,
        "event_validation": event_validation
    }
    }


def login_to_college_erp(session_id, payload):
    with requests.Session() as session:
        # Set the ASP.NET_SessionId cookie
        session.cookies.set("ASP.NET_SessionId", session_id, domain="erp.vce.ac.in")

        # Post to college login URL
        login_url = "https://erp.vce.ac.in/sinfo/Default.aspx"
        response = session.post(login_url, data=payload)
        # Now response.text contains the HTML of the dashboard or error
        return response