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
    except requests.RequestException as e:
        logger.error("HTTPError occurred during API call: %s", str(e))
        raise e


def fetch_transactions(*args, **kwargs):
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
    is_next = True
    while is_next:
        try:
            url = f"{connector.company_portal}/iclock/api/transactions/"
            params = dict(params, page=page)
            response = requests.get(url, params=params, headers=headers)
            if response.status_code == 200:
                transactions = response.json()
                for transaction in transactions["data"]:
                    filters = {"attendance_device_id": remove_non_numeric_chars(transaction["emp_code"])}
                    code = frappe.db.get_value("Employee", filters=filters, fieldname="name")
                    # TODO: Handle this case - if the employee is not found in ERPNext. checkin replica?
                    if not code:
                        continue
                    checkins.append(
                        {
                            "employee": code,
                            "time": transaction["punch_time"],
                            "log_type": transaction["punch_state_display"],
                        }
                    )

                is_next = bool(transactions["next"])
                page += 1
            else:
                logger.error(
                    "Failed to fetch transactions. Status code: %d",
                    response.status_code,
                )
                response.raise_for_status()
        except requests.RequestException as e:
            logger.error("HTTPError occurred during API call: %s", str(e))
            raise e

    return checkins


def insert_bulk_checkins(checkins) -> None:
    for checkin in checkins:
        try:
            checkin_doc = frappe.new_doc("Employee Checkin")
            checkin_doc.employee = checkin["employee"]
            checkin_doc.employee_name = frappe.db.get_value("Employee", checkin["employee"], "employee_name")
            checkin_doc.log_type = "IN" if checkin["log_type"] == "Check In" else "OUT"
            checkin_doc.time = checkin["time"]
            checkin_doc.insert(ignore_permissions=True)
        except Exception as e:
            logger.error("An error occurred while inserting checkin: %s", str(e))


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
            return connector
        else:
            logger.error("Failed to fetch token. Status code: %d", response.status_code)
    except requests.RequestException as e:
        logger.error("HTTPError occurred during API call: %s", str(e))
        raise e


def hourly_sync_devices():
    """
    Sync devices every hour.
    call:
    /iclock/api/transactions/?start_time={last_activity}&end_time={last_activity+1 hours}&terminal_alias={device_alias}
    """
    all_devices = frappe.get_all("BioTime Device", fields=["name", "device_id", "device_alias", "last_activity"])
    all_checkins = []
    for device in all_devices:
        start_time = device["last_activity"]
        end_time = (start_time + datetime.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        terminal_alias = device["device_alias"]
        device_checkins = fetch_transactions(
            start_time=start_time, end_time=end_time, terminal_alias=terminal_alias, page_size=1000
        )
        updated_last_activity = fetch_and_create_devices(device_id=device["device_id"])["last_activity"]
        frappe.db.set_value("BioTime Device", device["name"], "last_activity", updated_last_activity)
        frappe.db.set_value("BioTime Device", device["name"], "last_sync_request", frappe.utils.now_datetime())
        all_checkins.extend(device_checkins)

    return insert_bulk_checkins(all_checkins)
