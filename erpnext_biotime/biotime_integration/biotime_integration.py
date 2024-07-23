import datetime
import json
import re

import frappe
import requests

logger = frappe.logger("biotime", allow_site=True, file_count=50)


def remove_non_numeric_chars(string):
    # Use regular expression to remove non-numeric characters
    result = re.sub(r"\D", "", string)
    return result


def get_connector_with_headers() -> tuple:
    """
    Get the enabled connector and its headers.
    """
    enabled_connector = frappe.db.get_value("BioTime Connector", filters={"is_enabled": 1}, fieldname="name")
    connector = frappe.get_doc("BioTime Connector", enabled_connector)
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"JWT {connector.get_password('access_token')}",
    }
    # Check if the access token is expired
    try:
        url = f"{connector.company_portal}/iclock/api/terminals/"
        response = requests.get(url, headers=headers)
        # access token is valid
        if response.status_code == 200:
            return connector, headers
        # access token is expired
        elif response.status_code == 401:
            connector = refresh_connector_token(connector.name)
            headers["Authorization"]= f"Bearer {connector.get_password('access_token')}"
            return connector, headers
        else:
            raise Exception(f"Failed to fetch devices. Status code: {response.raise_for_status()}")
    except requests.RequestException as e:
        logger.error("HTTPError occurred during API call: %s", str(e))
        raise e


@frappe.whitelist()
def fetch_and_create_devices(device_id=None) -> None | dict:
    """
    Fetch devices from BioTime and create them in ERPNext. http://{ip}/iclock/api/terminals/
    Or fetch a single device by ID.
    """

    connector, headers = get_connector_with_headers()

    try:
        url = (
            f"{connector.company_portal}/iclock/api/terminals/"
            if not device_id
            else f"{connector.company_portal}/iclock/api/terminals/{device_id}/"
        )
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            if device_id:
                data = response.json()
                return {
                    "device_id": data["id"],
                    "device_name": data["terminal_name"],
                    "device_alias": data["alias"],
                    "device_ip_address": data["ip_address"],
                    "last_activity": data["last_activity"],
                    "last_sync_request": frappe.utils.now_datetime(),
                    "device_area": f"{data['area']['area_name']} - {data['area']['area_code']}",
                }
            devices = response.json()["data"]
            _created_devices = []
            for device in devices:
                try:
                    device_doc = frappe.new_doc("BioTime Device")
                    device_doc.device_id = device["id"]
                    device_doc.device_name = device["terminal_name"]
                    device_doc.device_alias = device["alias"]
                    device_doc.device_ip_address = device["ip_address"]
                    device_doc.last_activity = device["last_activity"]
                    device_doc.last_sync_request = frappe.utils.now_datetime()
                    device_doc.device_area = f"{device['area']['area_name']} - {device['area']['area_code']}"
                    device_doc.insert(ignore_permissions=True)
                    _created_devices.append(device_doc)
                except frappe.DuplicateEntryError:
                    logger.error("Device already exists in ERPNext: %s", device["terminal_name"])
                    continue
            frappe.msgprint(f"{len(_created_devices)} new device(s) created successfully")
        else:
            logger.error("Failed to fetch device(s). Status code: %d", response.status_code)
            return {}
    except requests.RequestException as e:
        logger.error("HTTPError occurred during API call: %s", str(e))
        raise e


def fetch_transactions(*args, **kwargs) -> tuple[list, list]:
    """
    Fetch transactions from BioTime. http://{ip}/iclock/api/transactions/
    """
    connector, headers = get_connector_with_headers()
    params = {
        k: v
        for k, v in kwargs.items()
        if k in ["start_time", "end_time", "page_size", "emp_code", "terminal_sn", "terminal_alias"]
    }

    page = 1
    checkins = []
    biotime_checkins = []
    is_next = True
    while is_next:
        try:
            url = f"{connector.company_portal}/iclock/api/transactions/"
            
            params = dict(params, page=page)
            response = requests.get(url, params=params, headers=headers)
            logger.error("Data Response %s", response.status_code)
            if response.status_code == 200:
                transactions = response.json()
                for transaction in transactions["data"]:
                    filters = {"attendance_device_id": transaction["emp_code"]}
                    code = frappe.db.get_value("Employee", filters=filters, fieldname="name")
                    _transaction_dict = {
                        "first_name": transaction["first_name"],
                        "last_name": transaction["last_name"],
                        "department": transaction["department"],
                        "position": transaction["position"],
                        "device_sn": transaction["terminal_sn"],
                        "device_alias": transaction["terminal_alias"],
                        "log_type": "IN" if transaction["punch_state_display"] == "Check In" else "OUT",
                        "time": transaction["punch_time"],
                    }
                    if code:
                        checkins.append(dict(_transaction_dict, employee=code))
                    else:
                        # Employee not found in ERPNext, save the transaction in a separate Checkin Log
                        biotime_checkins.append(dict(_transaction_dict, biotime_employee_code=transaction["emp_code"]))

                is_next = bool(transactions["next"])
                page += 1
            else:
                logger.error(
                    "Failed to fetch transactions. Status code: %d",
                    response.status_code,
                )
                response.raise_for_status()
        except requests.RequestException as e:
            trace = str(e) + frappe.get_traceback(with_context=True)
            logger.error("HTTPError occurred during API call: %s", trace)
            raise e

    return checkins, biotime_checkins


def insert_bulk_checkins(checkins) -> None:
    for checkin in checkins:
        try:
            checkin_doc = frappe.new_doc("Employee Checkin")
            checkin_doc.employee = checkin["employee"]
            checkin_doc.employee_name = frappe.db.get_value("Employee", checkin["employee"], "employee_name")
            checkin_doc.log_type = checkin["log_type"]
            checkin_doc.time = checkin["time"]
            checkin_doc.device_id = f"{checkin['device_sn']} - {checkin['device_alias']}"
            checkin_doc.insert(ignore_permissions=True)
            frappe.db.commit()

        except Exception as e:
           trace = str(e) + frappe.get_traceback(with_context=True)
           logger.error(trace)
        
def insert_bulk_biotime_checkins(checkins) -> None:
    for checkin in checkins:
        try:
            checkin_doc = frappe.new_doc("BioTime Checkins")
            checkin_doc.biotime_employee_code = checkin["biotime_employee_code"]
            checkin_doc.first_name = checkin["first_name"]
            checkin_doc.last_name = checkin["last_name"]
            checkin_doc.department = checkin["department"]
            checkin_doc.position = checkin["position"]
            checkin_doc.device_sn = checkin["device_sn"]
            checkin_doc.device_alias = checkin["device_alias"]
            checkin_doc.log_type = checkin["log_type"]
            checkin_doc.time = checkin["time"]
            checkin_doc.insert(ignore_permissions=True)
            frappe.db.commit()

        except Exception as e:
           trace = str(e) + frappe.get_traceback(with_context=True)
           logger.error(trace)

def refresh_connector_token(docname):
    headers = {"Content-Type": "application/json"}
    try:
        connector = frappe.get_doc("BioTime Connector", docname)
        url = f"{connector.company_portal}/jwt-api-token-auth/"
        non_hashed_password = frappe.get_doc("BioTime Connector", docname).get_password("password")
        response = requests.post(
            url,
            data=json.dumps({"username": connector.username, "password": non_hashed_password}),
            headers=headers,
        )
        if response.status_code == 200:
            access_token = response.json()["token"]
            connector.access_token = access_token
            connector.save(ignore_permissions=True)
            frappe.db.commit()
            return connector
        else:
            logger.error("Failed to fetch token. Status code: %d", response.status_code)
    except requests.RequestException as e:
        logger.error("HTTPError occurred during API call: %s", str(e))
        raise e


def hourly_sync_devices() -> None:
    """
    Sync devices every hour.
    call:
    /iclock/api/transactions/?start_time={last_activity}&end_time={last_activity+1 hours}&terminal_alias={device_alias}
    """
    all_devices = frappe.get_all("BioTime Device", fields=["name", "device_id", "device_alias", "last_activity"])
    
    for device in all_devices:
        all_checkins = []
        # checkins that are not in ERPNext
        all_biotime_checkins = []
        start_time = get_last_checkin(device)
        end_time = (start_time + datetime.timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
        terminal_alias = device["device_alias"]
        device_checkins, biotime_checkins = fetch_transactions(
            start_time=start_time, end_time=end_time, terminal_alias=terminal_alias, page_size=1000
        )        
        all_checkins.extend(device_checkins)
        all_biotime_checkins.extend(biotime_checkins)
            
        insert_bulk_checkins(all_checkins)
        insert_bulk_biotime_checkins(all_biotime_checkins)

       
def fetch_and_insert(*args, **kwargs):
    checkins, biotime_checkins = fetch_transactions(*args, **kwargs)

    print("checkins", checkins)
    print("biotime_checkins", biotime_checkins)

    insert_bulk_checkins(checkins)
    insert_bulk_biotime_checkins(biotime_checkins)


# patch


def insert_location(*args, **kwargs):
    """
    - get all Employee Checkins
    - build a dict of {employee-time-log_type: docname}
    """

    filters = {
        "time": ["between", [kwargs.get("start_time"), kwargs.get("end_time")]],
    }

    # A dict of {employee-time-log_type: docname}
    checkin_records = {}
    for doc in frappe.get_all("Employee Checkin", fields=["name", "employee", "time", "log_type"], filters=filters):
        checkin_records[f"{doc.employee}-{doc.time}-{doc.log_type}"] = doc.name

    print("Existing Employee Checkins", checkin_records)

    checkins, _ = fetch_transactions(
        start_time=kwargs.get("start_time"), end_time=kwargs.get("end_time"), page_size=10000
    )

    print("Returned Checkins", checkins)

    checkins_mapping = {}
    for checkin in checkins:
        location_value = f"{checkin['device_sn']} - {checkin['device_alias']}"
        checkins_mapping[f"{checkin['employee']}-{checkin['time']}-{checkin['log_type']}"] = location_value

    print("checkins_mapping", checkins_mapping)

    # update Employee Checkins
    for key, location in checkins_mapping.items():
        if key in checkin_records:
            frappe.db.set_value("Employee Checkin", checkin_records[key], "device_id", location)


def get_last_checkin(device: dict) -> datetime.datetime | None:
    
    last_timestamp = frappe.db.get_all(
        "Employee Checkin",
        filters={"device_id": ["like", "%" + device.get("device_alias") + "%"]},
        fields=["MAX(time) as time"],
    )
    
    return last_timestamp[0].get("time") if last_timestamp else device.get('last_activity')
