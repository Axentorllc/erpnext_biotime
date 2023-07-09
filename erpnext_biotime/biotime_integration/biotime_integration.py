import re
import frappe
import httpx
from frappe.utils.logger import get_logger

logger = frappe.logger("biotime", allow_site=True, file_count=50)


def remove_non_numeric_chars(string):
    # Use regular expression to remove non-numeric characters
    result = re.sub(r'\D', '', string)
    return result


def get_connector():
    enabled_connector = frappe.db.get_value("BioTime Connector", filters={"is_enabled": 1}, fieldname="name")
    connector = frappe.get_doc("BioTime Connector", enabled_connector)
    return connector


def fetch_transactions(start_time, end_time, page_size=200):
    connector = get_connector()
    headers = {"Content-Type": "application/json", "Authorization": f"JWT {connector.get_password('access_token')}"}

    page = 1
    checkins = []
    with httpx.Client(headers=headers) as client:
        is_next = True
        while is_next:
            try:
                url = f"{connector.company_portal}/iclock/api/transactions/"
                params = {
                    "page": page,
                    "page_size": page_size,
                    "start_time": start_time,
                    "end_time": end_time
                }
                response = client.get(url, params=params)
                print("response", response)
                if response.status_code == 200:
                    transactions = response.json()
                    print("transactions", transactions["next"])
                    for transaction in transactions["data"]:
                        filters = {"attendance_device_id": remove_non_numeric_chars(transaction["emp_code"])}
                        code = frappe.db.get_value("Employee", filters=filters, fieldname="name")
                        if not code:
                            continue
                        checkins.append({
                            "employee": code,
                            "time": transaction["punch_time"],
                            "log_type": transaction["punch_state_display"]
                        })

                    is_next = bool(transactions["next"])
                    page += 1
                else:
                    print("Failed to fetch transactions. Status code: %d", response.status_code)
                    logger.error("Failed to fetch transactions. Status code: %d", response.status_code)
                    break
            except httpx.HTTPError as e:
                print("HTTPError occurred during API call: %s", str(e), page)
                logger.error("HTTPError occurred during API call: %s", str(e))
                break
            except Exception as e:
                print("An error occurred during API call: %s", str(e))
                logger.error("An error occurred during API call: %s", str(e))
                break

    return insert_bulk_checkins(checkins)


def insert_bulk_checkins(checkins):
    print("checkins", checkins)
    for checkin in checkins:
        try:
            checkin_doc = frappe.new_doc("Employee Checkin")
            checkin_doc.employee = checkin["employee"]
            checkin_doc.employee_name = frappe.db.get_value("Employee", checkin["employee"], "employee_name")
            checkin_doc.log_type = "IN" if checkin["log_type"] == "Check In" else "OUT"
            checkin_doc.time = checkin["time"]
            checkin_doc.insert(ignore_permissions=True)
        except Exception as e:
            print("Error", e)
    frappe.db.commit()
