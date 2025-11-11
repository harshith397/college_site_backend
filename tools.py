from bs4 import BeautifulSoup
from typing import Dict, Any
import httpx
from exceptions import CollegePortalError

LOGIN_URL = "https://erp.vce.ac.in/sinfo/Default.aspx"
DASHBOARD_URL = "https://erp.vce.ac.in/sinfo/DashBoard.aspx"

# Add this helper function inside get_attendance_subjects
def format_oe_subject(subject: str) -> str:
    """Format open elective subjects to the required pattern"""
    if subject.startswith('OE-'):
        # Extract the part after OE- and before (OE)
        base = subject.replace('OE-', '').replace('(OE)', '').strip()
        return f"{base}(OE)-OE"
    return subject

def to_number(value):
    if isinstance(value, str):
        value = value.strip()
        if value == '-' or value == '':
            return '-' if value == '-' else 0
        try:
            return int(value) if '.' not in value else float(value)
        except ValueError:
            return value
    return value


def convert_values_to_number(d):
    """
    Recursively converts string numbers to int/float in a nested dict,
    while leaving non-numeric strings untouched.
    """
    for k, v in d.items():
        if isinstance(v, dict):
            convert_values_to_number(v)
        elif isinstance(v, str):
            v = v.strip()
            if not v:  # empty string, leave as-is
                continue
            try:
                # Try int first
                int_val = int(v)
                d[k] = int_val
            except ValueError:
                try:
                    # Try float
                    float_val = float(v)
                    d[k] = float_val
                except ValueError:
                    # Keep original string (like "M.Harshith")
                    pass
        else:
            # Already int/float/bool, leave as-is
            pass

    return d



def extract_att_summary(html: str) -> dict:
    """
    Extracts the first row of attendance summary info from the dashboard HTML.

    Steps:
    1. Locate the div with id 'divAttSummary', which contains the attendance table.
    2. Skip the header row and read the first data row (current semester summary).
    3. Extract key fields like Year, Sem., Status, Class Start/End Dates.
    4. Extract Attendance link from the 7th cell if present.
    5. Extract Marks link from the 8th cell if present.

    Raises:
        CollegePortalError: When expected HTML sections or data are missing, indicating
        either a site change, backend scraping issue, or missing data.
    
    Returns:
        dict: Attendance summary data including optional Attendance and Marks links.
    """
    # Parse the HTML content
    soup = BeautifulSoup(html, "lxml")

    # Step 1: Locate the main attendance container
    div_att = soup.find("div", id="divAttSummary")
    if not div_att:
        raise CollegePortalError(500, "Unable to find Attendance summary section")

    # Step 2: Extract table rows, skipping the header row
    rows = div_att.select("table tr")[1:]
    if not rows:
        raise CollegePortalError(500, "Unable to find attendance data rows")

    first_row = rows[0]
    cells = first_row.find_all("td")
    if len(cells) < 6:
        raise CollegePortalError(500, "Incomplete attendance row data")

    # Step 3: Extract mandatory visible fields from the first row
    row_data = {
        "Year": cells[0].get_text(strip=True),
        "Sem.": cells[1].get_text(strip=True),
        "Academic Year": cells[2].get_text(strip=True),
        "Status": cells[3].get_text(strip=True),
        "Class Start Date": cells[4].get_text(strip=True),
        "Class End Date": cells[5].get_text(strip=True),
    }

    # Step 4: Extract Attendance link (optional)
    if len(cells) > 6:
        att_tag = cells[6].find("a", onclick=True)
        if att_tag and "popUp" in att_tag["onclick"]:
            raw_link = att_tag["onclick"].split("popUp(")[1].split("'")[1]
            row_data["Attendance Link"] = "https://erp.vce.ac.in/sinfo/" + raw_link
        else:
            print("Attendance link not found in the first row - skipping")

    # Step 5: Extract Marks link (optional)
    if len(cells) > 7:
        marks_tag = cells[7].find("a", onclick=True)
        if marks_tag and "popUp" in marks_tag["onclick"]:
            raw_link = marks_tag["onclick"].split("popUp(")[1].split("'")[1]
            row_data["Marks Link"] = raw_link
        else:
            print("Marks link not found in the first row - skipping")
    
    return row_data



def get_student_info(html: str) -> Dict[str, str]:
    """
    Extract student information (like Name, Roll No, Branch, etc.)
    from the ERP dashboard HTML.

    Steps:
    1. Parse HTML safely using BeautifulSoup.
    2. Locate <div id="divStudentInfo"> and the first table inside it.
    3. Read key-value pairs from table rows (th/td).
    4. Convert any numeric-looking strings to actual numbers.
    """
    # ---- Validate HTML input ----
    if not html or len(html.strip()) == 0:
        raise CollegePortalError(status_code=500, message="Empty or invalid HTML received from ERP.")

    # ---- Parse HTML ----
    soup = BeautifulSoup(html, "lxml")
    student_info_div = soup.find("div", {"id": "divStudentInfo"})

    if not student_info_div:
        raise CollegePortalError(status_code=500, message="Student information section not found in ERP dashboard.")

    table = student_info_div.find("table")
    if not table:
        raise CollegePortalError(status_code=500, message="Student information table not found in ERP dashboard.")

    data = {}

    # ---- Extract table rows ----
    for row in table.find_all("tr"):
        cols = row.find_all(["th", "td"])
        if len(cols) < 2:
            continue

        key = cols[0].get_text(strip=True)
        value = cols[1].get_text(strip=True)

        if key and value:
            data[key] = value

    if not data:
        raise CollegePortalError(status_code=500, message="No student data found in ERP dashboard table.")

    # ---- Postprocess values (convert numeric strings) ----
    processed = convert_values_to_number(data)

    return processed



def get_attendance_subjects(html: str) -> tuple:
    """
    Parse attendance page HTML and return subject-wise summary JSON.
    Logic taken from the current code that reads TblDispAttSubSummary.
    Returns a dict keyed by row titles (Held Classes, Presentees, Absentees, Extra Classes)
    with values being dicts mapping subject -> value.
    """
    soup = BeautifulSoup(html, "lxml")
    sub_attendance_data: Dict[str, Dict[str, str]] = {}
    total_attendance_data: Dict[str, Dict[str, str]] = {}

    img_tag = soup.find('img')
    image_url = img_tag['src'] if img_tag and 'src' in img_tag.attrs else None
      
    # Find the outer summary tables by id
    sub_summary_table = soup.find("table", {"id": "TblDispAttSubSummary"})
    total_summary_table = soup.find("table", {"id": "TblDispAttSummary"})

    # If either is missing, return empty/partial results (caller can handle)
    if not sub_summary_table or not total_summary_table:
        return sub_attendance_data, total_attendance_data, image_url

    # The visible content is inside an inner table with class 'tableclass'
    sub_inner_table = sub_summary_table.find("table", class_='tableclass')
    total_inner_table = total_summary_table.find("table", class_="tableclass")

    if not sub_inner_table or not total_inner_table:
        return sub_attendance_data, total_attendance_data, image_url

    sub_rows = sub_inner_table.find_all("tr")
    total_rows = total_inner_table.find_all("tr")

    if not sub_rows or not total_rows:
        return convert_values_to_number(sub_attendance_data), convert_values_to_number(total_attendance_data), image_url

    # header row contains subjects; first header cell is a row-title label (skip it)
    sub_headers = [td.get_text(strip=True) for td in sub_rows[0].find_all("td")]
    sub_type_headers = [td.get_text(strip=True) for td in total_rows[0].find_all("td")]
    # e.g. ['Classes', 'DS', 'HVPE-II', ...]
    # build full lists first, then remove any subject that starts with "ECA"
    full_subject_names = [format_oe_subject(name.strip()) for name in sub_headers[1:]]
    # indices to keep (exclude subjects starting with ECA)
    keep_indices = [i for i, name in enumerate(full_subject_names) if not name.upper().startswith("ECA")]
    subject_names = [full_subject_names[i] for i in keep_indices]

    full_sub_type_names = [format_oe_subject(name.strip()) for name in sub_type_headers[2:]]
    total_keep_indices = [i for i, name in enumerate(full_sub_type_names) if not name.upper().startswith("ECA")]
    sub_type_names = [full_sub_type_names[i] for i in total_keep_indices]

    # Build subject-wise data: row_title -> { subject: value, ... }
    for row in sub_rows[1:]:
        cells = row.find_all("td")
        if not cells:
            continue
        row_title = cells[0].get_text(strip=True)  # e.g. "Held Classes"
        values = [td.get_text(strip=True) for td in cells[1:]]
        # Filter values using the same indices we filtered headers with
        filtered_values = [values[i] for i in keep_indices if i < len(values)]
        # Zip subjects -> filtered_values (if counts mismatch, zip will trim to shortest)
        sub_attendance_data[row_title] = convert_values_to_number(dict(zip(subject_names, filtered_values)))

    # Build totals/summary in a similar manner; note index offsets differ in this table
    for row in total_rows[1:]:
        cells = row.find_all("td")
        if not cells:
            continue
        # In the total summary table the label is in cells[1] and values start from cells[2]
        row_title = cells[1].get_text(strip=True)  # e.g. "Held Classes"
        values = [td.get_text(strip=True) for td in cells[2:]]
        filtered_values = [values[i] for i in total_keep_indices if i < len(values)]
        total_attendance_data[row_title] = convert_values_to_number(dict(zip(sub_type_names, filtered_values)))

    return sub_attendance_data, total_attendance_data, image_url


def get_current_sem(html: str) -> Dict[str, str]:
    """
    Build the 'current_sem' dict for dashboard using extract_att_summary()
    to obtain the Attendance/Marks links (overrides previous manual link-extraction).
    It still extracts visible cell text for columns; the Attendance/Marks link fields
    come from extract_att_summary to avoid duplicate/wrong links.
    """
    soup = BeautifulSoup(html, "lxml")
    div = soup.find("div", {"id": "divAttSummary"})
    if not div:
        return {}

    rows = div.select("table tr")
    if len(rows) < 2:
        return {}

    header_cells = [td.get_text(strip=True) for td in rows[0].find_all("td")]
    first_row_cells = rows[1].find_all("td")

    current_sem = {}
    # populate values from visible text (prefer non-anchor text to avoid 'view' anchor values)
    for i, cell in enumerate(first_row_cells):
        header = header_cells[i]

        # Skip Marks column entirely (we'll get the canonical link from extract_att_summary)
        if header.lower().startswith("marks"):
            continue

        # prefer non-anchor direct text nodes to avoid capturing the 'view' anchor text
        non_anchor_texts = [
            t.strip()
            for t in cell.find_all(string=True, recursive=False)
            if t.strip()
        ]
        text = " ".join(non_anchor_texts) if non_anchor_texts else cell.get_text(strip=True)
        current_sem[header] = text

    # Use extract_att_summary to get canonical Attendance Link / Marks Link and other fields
    try:
        dash_row = extract_att_summary(html)
        # merge/override link fields if present
        if "Attendance Link" in dash_row:
            current_sem["Attendance Link"] = dash_row["Attendance Link"]
        if "Marks Link" in dash_row:
            current_sem["Marks Link"] = "https://erp.vce.ac.in/sinfo/" + dash_row["Marks Link"]
        # also merge other basic fields (Year, Sem., etc.)
        for k in ("Year", "Sem.", "Academic Year", "Status", "Class Start Date", "Class End Date"):
            if k in dash_row:
                current_sem[k] = dash_row[k]
    except Exception:
        # if extract_att_summary fails, keep current_sem as-is (caller may handle missing links)
        pass

    return convert_values_to_number(current_sem)



def parse_marks_table(html: str) -> dict:
    soup = BeautifulSoup(html, 'lxml')
    gender_td = soup.find("td", string=lambda text: text and "Gender" in text)
    gender = "-"
#   Move two <td> forward to get the value
    if gender_td:
        gender_value_td = gender_td.find_next_sibling("td").find_next_sibling("td")
        gender = gender_value_td.get_text(strip=True).replace(":", "").replace("\xa0", "")
    
    tables = soup.find_all('table', class_='tableclass')
    if len(tables) < 2:
        raise ValueError("Less than two tables with class='tableclass' found.")
    
    table = tables[1]
    rows = table.find_all('tr')
    
    # Parse Row 1: Main headers
    main_header_cells = rows[1].find_all('td')
    main_headers = [cell.get_text(strip=True).replace('\n', ' ') for cell in main_header_cells]
    
    # Parse Row 2: Sub headers
    sub_header_cells = rows[2].find_all('td')
    sub_headers = [cell.get_text(strip=True).replace('\n', ' ') for cell in sub_header_cells]
    
    # Build component type mapping (which main header each sub-header belongs to)
    component_map = []  # List of (main_header, sub_header_type)
    sub_idx = 0
    
    for main_header in main_headers:
        if main_header in ['S.No', 'Subject Name']:
            component_map.append((main_header, 'single'))
            sub_idx += 1
        elif main_header == 'ExternalGrades':
            component_map.append((main_header, 'grade'))
            component_map.append((main_header, 'points'))
            component_map.append((main_header, 'credits'))
            sub_idx += 3
        else:
            # Int1, Int2, Quiz1, Asst1, SessionalMarks, etc.
            component_map.append((main_header, 'max'))
            component_map.append((main_header, 'secured'))
            sub_idx += 2
    
    subjects = []
    data_rows = rows[3:-2]
    
    for row in data_rows:
        cells = row.find_all('td')
        if len(cells) != len(component_map):
            continue
        
        # Initialize subject structure
        subject = {
            "s_no": 0,
            "name": "",
            "credits": 0,
            "components": {
                "assignment": [],
                "quiz": [],
                "internal": [],
                "sessional": []
            },
            "grade": "-",
            "grade_points": 0
        }
        
        # Temporary storage for component pairs
        temp_components = {}
        
        for idx, (main_header, sub_type) in enumerate(component_map):
            cell_value = to_number(cells[idx].get_text(strip=True))
            
            if main_header == 'S.No':
                subject["s_no"] = cell_value
            elif main_header == 'Subject Name':
                subject["name"] = cell_value
            elif main_header == 'ExternalGrades':
                if sub_type == 'grade':
                    subject["grade"] = cell_value
                elif sub_type == 'points':
                    subject["grade_points"] = cell_value
                elif sub_type == 'credits':
                    subject["credits"] = cell_value
            else:
                # Handle Int1, Int2, Quiz1, Asst1, SessionalMarks
                if main_header not in temp_components:
                    temp_components[main_header] = {}
                
                if sub_type == 'max':
                    temp_components[main_header]['max'] = cell_value
                elif sub_type == 'secured':
                    temp_components[main_header]['secured'] = cell_value
        
        # Convert temp_components to proper component arrays
        for comp_name, values in temp_components.items():
            if comp_name.startswith('Int'):
                subject["components"]["internal"].append({
                    "name": comp_name,
                    "max": values.get('max', 0),
                    "secured": values.get('secured', 0)
                })
            elif comp_name.startswith('Quiz'):
                subject["components"]["quiz"].append({
                    "name": comp_name,
                    "max": values.get('max', 0),
                    "secured": values.get('secured', 0)
                })
            elif comp_name.startswith('Asst'):
                subject["components"]["assignment"].append({
                    "name": comp_name,
                    "max": values.get('max', 0),
                    "secured": values.get('secured', 0)
                })
            elif comp_name.startswith('SessionalMarks'):
                max_val = values.get('max', 0)
                secured_val = values.get('secured', 0)
                # Only add if not empty
                if max_val not in ['-', 0] and secured_val not in ['-', 0]:
                    subject["components"]["sessional"].append({
                        "name": comp_name,
                        "max": max_val,
                        "secured": secured_val
                    })
        
        subjects.append(subject)
    
    # Parse Total row (-2)
    total_cells = rows[-2].find_all('td')
    total_values = []
    for cell in total_cells:
        colspan = int(cell.get('colspan', 1))
        if colspan == 2:  # Skip "Total" label
            continue
        total_values.append(to_number(cell.get_text(strip=True)))
    
    # Parse Percentage row (-1)
    percent_cells = rows[-1].find_all('td')
    percent_values = {}
    percent_idx = 0
    
    for cell in percent_cells:
        text = cell.get_text(strip=True)
        colspan = int(cell.get('colspan', 1))
        
        if text == "Percentage":
            continue
        
        if text.startswith("SGPA"):
            percent_values['sgpa'] = text
        else:
            # Map to main headers (skipping S.No and Subject Name)
            main_header_idx = percent_idx + 2  # +2 to skip S.No and Subject Name
            if main_header_idx < len(main_headers):
                percent_values[main_headers[main_header_idx]] = to_number(text)
            percent_idx += 1
    
    # Build summary
    summary = {
        "total_marks": {},
        "sgpa": percent_values.get('sgpa', '-')
    }
    
    # Map total values to components
    total_idx = 0
    for main_header in main_headers[2:]:  # Skip S.No and Subject Name
        if main_header == 'ExternalGrades':
            # Skip Grade, Points, Credits in totals (not meaningful)
            total_idx += 3
        elif main_header.startswith(('Int', 'Quiz', 'Asst', 'SessionalMarks')):
            max_val = total_values[total_idx] if total_idx < len(total_values) else 0
            secured_val = total_values[total_idx + 1] if total_idx + 1 < len(total_values) else 0
            percentage = percent_values.get(main_header, None)
            
            summary["total_marks"][main_header.lower()] = {
                "max": max_val,
                "secured": secured_val,
                "percentage": percentage
            }
            total_idx += 2
    
    return {
        "subjects": subjects,
        "summary": summary
    },gender


async def fetch_login_hidden_fields() -> Dict[str, Any]:
    """
    Fetch hidden ASP.NET fields from the college ERP login page (async version).
    Raises CollegePortalError on any failure.
    Returns: dict with hidden fields
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/140.0.0.0 Safari/537.36"
        ),
        "Referer": LOGIN_URL,
    }

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(LOGIN_URL, headers=headers)
            
            if r.status_code != 200:
                raise CollegePortalError(
                    status_code=503,
                    message="College ERP portal unreachable.",
                    detail=f"Returned status {r.status_code}",
                )

            soup = BeautifulSoup(r.text, "lxml")
            hidden_fields = {
                inp.get("name"): inp.get("value", "")
                for inp in soup.find_all("input", {"type": "hidden"})
                if inp.get("name")
            }

            if not hidden_fields:
                raise CollegePortalError(
                    status_code=500,
                    message="No hidden fields found.",
                    detail="ERP page structure may have changed.",
                )

            return hidden_fields

    except httpx.TimeoutException:
        raise CollegePortalError(
            status_code=503,
            message="ERP portal timeout.",
            detail="College ERP took too long to respond.",
        )

    except httpx.RequestError as e:
        raise CollegePortalError(
            status_code=503,
            message="Network error while connecting to ERP.",
            detail=str(e),
        )

    except Exception as e:
        raise CollegePortalError(
            status_code=500,
            message="Unexpected error while parsing ERP login page.",
            detail=str(e),
        )
    