import re
import frappe
import httpx


def remove_non_numeric_chars(string):
    # Use regular expression to remove non-numeric characters
    result = re.sub(r'\D', '', string)
    return result


def get_connector():
    enabled_connector = frappe.db.get_value("BioTime Connector", filters={"is_enabled": 1}, fieldname="name")
    connector = frappe.get_doc("BioTime Connector", enabled_connector)
    return connector


def fetch_transactions(start_time, end_time, page, page_size=200):
    connector = get_connector()
    url = f"{connector.company_portal}/iclock/api/transactions/?page={page}&page_size={page_size}&start_time={start_time}&end_time={end_time}"
    headers = {"Content-Type": "application/json", "Authorization": f"JWT {connector.get_password('access_token')}"}

    with httpx.Client(headers=headers) as client:
        response = client.get(url)
        if response.status_code == 200:
            transactions = response.json()
            return transactions


def get_all_transactions(start_time, end_time):
    all_transactions = []
    page = 1

    is_next = True
    while is_next:
        transactions = fetch_transactions(start_time, end_time, page)
        print("transactions", transactions["next"])
        if transactions:
            for transaction in transactions["data"]:
                code = frappe.db.get_value("Employee", filters={"attendance_device_id": remove_non_numeric_chars(transaction["emp_code"])}, fieldname="name")
                print(">>>>", transaction["emp_code"], transaction["punch_time"], transaction["emp_code"], remove_non_numeric_chars(transaction["emp_code"]))
                if not code:
                    continue
                checkin = frappe.new_doc("Employee Checkin")
                checkin.employee = code
                checkin.employee_name = frappe.db.get_value("Employee", code, "employee_name")
                checkin.log_type = "IN"
                checkin.time = transaction["punch_time"]
                checkin.insert(ignore_permissions=True)

            frappe.db.commit()
            page += 1
        if not transactions["next"]:
            is_next = False
    return all_transactions
