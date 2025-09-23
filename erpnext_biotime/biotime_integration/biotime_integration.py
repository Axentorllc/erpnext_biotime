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
    Get the enabled connector and its headers with improved error handling.
    """
    enabled_connector = frappe.db.get_value("BioTime Connector", filters={"is_enabled": 1}, fieldname="name")
    if not enabled_connector:
        raise Exception("No enabled BioTime Connector found")
    
    connector = frappe.get_doc("BioTime Connector", enabled_connector)
    access_token = connector.get_password('access_token')
    
    # If no access token exists, get a new one
    if not access_token:
        logger.info("No access token found, refreshing token for connector: %s", connector.name)
        connector = refresh_connector_token(connector.name)
        access_token = connector.get_password('access_token')
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"JWT {access_token}",
    }
    
    # Check if the access token is valid by making a test request
    try:
        url = f"{connector.company_portal}/iclock/api/terminals/"
        response = requests.get(url, headers=headers, timeout=3000)
        
        # access token is valid
        if response.status_code == 200:
            return connector, headers
        # access token is expired or invalid
        elif response.status_code == 401:
            logger.error("Access token expired for connector: %s, refreshing token", connector.name)
            connector = refresh_connector_token(connector.name)
            # Update headers with new token and keep JWT format
            headers["Authorization"] = f"JWT {connector.get_password('access_token')}"
            return connector, headers
        else:
            logger.error("Failed to validate token. Status code: %d, Response: %s", response.status_code, response.text)
            response.raise_for_status()
    except requests.Timeout:
        logger.error("Timeout occurred while validating token for connector: %s", connector.name)
        raise Exception("Request timeout while validating authentication token")
    except requests.RequestException as e:
        logger.error("HTTPError occurred during token validation: %s", str(e))
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
        response = requests.get(url, headers=headers, timeout=3000)
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
    Fetch transactions from BioTime with improved error handling and retry logic.
    """
    max_retries = 3
    retry_count = 0
    
    while retry_count < max_retries:
        try:
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
                url = f"{connector.company_portal}/iclock/api/transactions/"
                params_with_page = dict(params, page=page)
                response = requests.get(url, params=params_with_page, headers=headers, timeout=3000)
                
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
                elif response.status_code == 401:
                    # Token expired, retry with fresh token
                    logger.error("Token expired during transaction fetch, retrying with fresh token")
                    retry_count += 1
                    if retry_count >= max_retries:
                        raise Exception("Max retries exceeded for authentication")
                    continue
                else:
                    logger.error("Failed to fetch transactions. Status code: %d, Response: %s", 
                               response.status_code, response.text)
                    response.raise_for_status()
            
            return checkins, biotime_checkins
            
        except requests.RequestException as e:
            retry_count += 1
            if retry_count >= max_retries:
                trace = str(e) + frappe.get_traceback(with_context=True)
                logger.error("HTTPError occurred during API call after %d retries: %s", max_retries, trace)
                raise e
            else:
                logger.error("Request failed, retrying (%d/%d): %s", retry_count, max_retries, str(e))
                continue


def insert_bulk_checkins(checkins) -> None:
    """
    Insert checkins with improved error handling and duplicate prevention.
    """
    if not checkins:
        return
        
    checkin_docs = []
    successful_inserts = 0
    failed_inserts = 0

    for checkin in checkins:
        try:
            # Check for duplicate checkins
            existing_checkin = frappe.db.exists("Employee Checkin", {
                "employee": checkin["employee"],
                "time": checkin["time"],
                "log_type": checkin["log_type"]
            })
            
            if existing_checkin:
                logger.error("Duplicate checkin found for employee %s at %s, skipping", 
                           checkin["employee"], checkin["time"])
                continue
                
            checkin_doc = frappe.new_doc("Employee Checkin")
            checkin_doc.employee = checkin["employee"]
            checkin_doc.employee_name = frappe.db.get_value("Employee", checkin["employee"], "employee_name")
            checkin_doc.log_type = checkin["log_type"]
            checkin_doc.time = checkin["time"]
            checkin_doc.device_id = f"{checkin['device_sn']} - {checkin['device_alias']}"
            checkin_doc.insert(ignore_permissions=True)
            checkin_docs.append(checkin_doc)
            successful_inserts += 1

        except Exception as e:
            failed_inserts += 1
            trace = str(e) + frappe.get_traceback(with_context=True)
            logger.error("Failed to insert checkin for employee %s: %s", 
                        checkin.get("employee", "Unknown"), trace)
    
    if successful_inserts > 0:
        logger.error("Successfully inserted %d checkins", successful_inserts)
    if failed_inserts > 0:
        logger.error("Failed to insert %d checkins", failed_inserts)

        
def insert_bulk_biotime_checkins(checkins) -> None:
    """
    Insert biotime checkins with improved error handling and duplicate prevention.
    """
    if not checkins:
        return
        
    successful_inserts = 0
    failed_inserts = 0
    
    for checkin in checkins:
        try:
            # Check for duplicate biotime checkins
            existing_checkin = frappe.db.exists("BioTime Checkins", {
                "biotime_employee_code": checkin["biotime_employee_code"],
                "time": checkin["time"],
                "log_type": checkin["log_type"]
            })
            
            if existing_checkin:
                logger.error("Duplicate biotime checkin found for employee code %s at %s, skipping", 
                           checkin["biotime_employee_code"], checkin["time"])
                continue
                
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
            successful_inserts += 1

        except Exception as e:
            failed_inserts += 1
            trace = str(e) + frappe.get_traceback(with_context=True)
            logger.error("Failed to insert biotime checkin for employee code %s: %s", 
                        checkin.get("biotime_employee_code", "Unknown"), trace)
    
    if successful_inserts > 0:
        logger.error("Successfully inserted %d biotime checkins", successful_inserts)
    if failed_inserts > 0:
        logger.error("Failed to insert %d biotime checkins", failed_inserts)


def refresh_connector_token(docname):
    """
    Refresh the connector token with improved error handling.
    """
    headers = {"Content-Type": "application/json"}
    try:
        connector = frappe.get_doc("BioTime Connector", docname)
        url = f"{connector.company_portal}/jwt-api-token-auth/"
        non_hashed_password = connector.get_password("password")
        
        if not non_hashed_password:
            raise Exception("No password found for BioTime Connector")
            
        response = requests.post(
            url,
            data=json.dumps({"username": connector.username, "password": non_hashed_password}),
            headers=headers,
            timeout=3000
        )
        
        if response.status_code == 200:
            access_token = response.json()["token"]
            connector.access_token = access_token
            connector.save(ignore_permissions=True)
            frappe.db.commit()
            logger.error("Successfully refreshed token for connector: %s", connector.name)
            return connector
        else:
            logger.error("Failed to refresh token. Status code: %d, Response: %s", 
                        response.status_code, response.text)
            raise Exception(f"Failed to refresh token: {response.status_code}")
    except requests.RequestException as e:
        logger.error("HTTPError occurred during token refresh: %s", str(e))
        raise e


def hourly_sync_devices() -> None:
    """
    Sync devices every hour with improved error handling and device status updates.
    """
    try:
        all_devices = frappe.get_all("BioTime Device", fields=["name", "device_id", "device_alias", "last_activity", "last_sync_request"])
        
        if not all_devices:
            logger.error("No devices found for sync")
            return
            
        logger.error("Starting sync for %d devices", len(all_devices))
        successful_syncs = 0
        failed_syncs = 0
        
        for device in all_devices:
            try:
                logger.error("Syncing device: %s (ID: %s)", device["device_alias"], device["device_id"])
                
                device_checkins, biotime_checkins = device_sync_interval(device)
                
                if device_checkins or biotime_checkins:
                    insert_bulk_checkins(device_checkins)
                    insert_bulk_biotime_checkins(biotime_checkins)
                    
                    # Update device sync timestamp
                    frappe.db.set_value("BioTime Device", device["name"], "last_sync_request", frappe.utils.now_datetime())
                    
                    logger.error("Successfully synced device %s: %d employee checkins, %d biotime checkins", 
                              device["device_alias"], len(device_checkins), len(biotime_checkins))
                else:
                    logger.error("No new checkins found for device %s", device["device_alias"])
                    
                successful_syncs += 1
                
            except Exception as e:
                failed_syncs += 1
                logger.error("Failed to sync device %s (ID: %s): %s", 
                           device.get("device_alias", "Unknown"), device.get("device_id", "Unknown"), str(e))
                continue
        
        frappe.db.commit()
        logger.error("Sync completed: %d successful, %d failed", successful_syncs, failed_syncs)
        
    except Exception as e:
        logger.error("Critical error in hourly_sync_devices: %s", str(e))
        raise e


def device_sync_interval(device: dict) -> tuple[list, list]:
    """
    Sync a single device with improved logic and error handling.
    """
    max_hours = 24
    num_hours = 2
    device_id = device["device_id"]
    terminal_alias = device["device_alias"]
    
    # Get the last sync time
    start_time = get_last_checkin(device)
    if not start_time:
        logger.error("No last checkin time found for device %s, using last_activity", terminal_alias)
        start_time = device.get('last_activity')
        if not start_time:
            logger.error("No last_activity found for device %s, using 24 hours ago", terminal_alias)
            start_time = frappe.utils.now_datetime() - datetime.timedelta(hours=24)
    
    # Convert to datetime if it's a string
    if isinstance(start_time, str):
        start_time = frappe.utils.get_datetime(start_time)
    
    while num_hours <= max_hours:
        try:
            end_time = start_time + datetime.timedelta(hours=num_hours)
            
            # Don't sync future times
            if end_time > frappe.utils.now_datetime():
                end_time = frappe.utils.now_datetime()
            
            start_time_str = start_time.strftime("%Y-%m-%d %H:%M:%S")
            end_time_str = end_time.strftime("%Y-%m-%d %H:%M:%S")
            
            logger.error("Fetching transactions for device %s from %s to %s", 
                       terminal_alias, start_time_str, end_time_str)

            device_checkins, biotime_checkins = fetch_transactions(
                start_time=start_time, 
                end_time=end_time, 
                terminal_alias=terminal_alias
            )
            
            total_checkins = len(device_checkins) + len(biotime_checkins)
            
            if total_checkins == 0 and num_hours < max_hours:
                num_hours *= 2
                logger.error("No checkins found for device %s, expanding interval to %d hours", 
                           terminal_alias, num_hours)
                continue
            
            logger.error("Found %d total checkins for device %s (%d employee, %d biotime)", 
                       total_checkins, terminal_alias, len(device_checkins), len(biotime_checkins))
            
            return device_checkins, biotime_checkins

        except Exception as e:
            logger.error("Error syncing device ID %s with %d hour interval: %s", 
                        device_id, num_hours, str(e))
            num_hours *= 2
            if num_hours > max_hours:
                break
            continue
            
    # Return empty lists if no data found after all attempts
    logger.error("No checkins found for device %s after trying up to %d hours", terminal_alias, max_hours)
    return [], []


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
    """
    Get the last checkin time for a device with improved error handling.
    """
    try:
        last_timestamp = frappe.db.get_all(
            "Employee Checkin",
            filters={"device_id": ["like", "%" + device.get("device_alias") + "%"]},
            fields=["MAX(time) as time"],
        )
        
        if last_timestamp and last_timestamp[0].get("time"):
            return last_timestamp[0].get("time")
        else:
            # Fallback to device's last_activity
            last_activity = device.get('last_activity')
            if last_activity:
                if isinstance(last_activity, str):
                    return frappe.utils.get_datetime(last_activity)
                return last_activity
            else:
                # If no last activity, use 24 hours ago as default
                return frappe.utils.now_datetime() - datetime.timedelta(hours=24)
                
    except Exception as e:
        logger.error("Error getting last checkin for device %s: %s", device.get("device_alias"), str(e))
        # Return 24 hours ago as fallback
        return frappe.utils.now_datetime() - datetime.timedelta(hours=24)

def fetch_transactions_by_id(last_synced_id=None, page_size=1000) -> tuple[list, list]:
    """
    Fetch transactions from BioTime using ID-based pagination.
    This is more reliable than date-based queries for hourly sync.
    """
    max_retries = 3
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            connector, headers = get_connector_with_headers()
            
            # Calculate page number based on last synced ID
            page = 1
            if last_synced_id and last_synced_id > 0:
                page = (last_synced_id // page_size) + 1
                
            url = f"{connector.company_portal}/iclock/api/transactions/"
            params = {
                "page": page,
                "page_size": page_size
            }
            
            checkins = []
            biotime_checkins = []
            
            response = requests.get(url, params=params, headers=headers, timeout=3000)
            if response.status_code == 200:
                transactions = response.json()
                
                for i, transaction in enumerate(transactions.get("data", [])):
                    
                    # Skip transactions with ID <= last_synced_id
                    if last_synced_id and transaction.get("id", 0) <= last_synced_id:
                        continue
                    
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
                        "transaction_id": transaction.get("id")  # Store transaction ID
                    }
                    
                    if code:
                        checkins.append(dict(_transaction_dict, employee=code))
                    else:
                        biotime_checkins.append(dict(_transaction_dict, biotime_employee_code=transaction["emp_code"]))
                
                logger.error(f"Finished processing all transactions. Returning {len(checkins)} employee checkins and {len(biotime_checkins)} biotime checkins")
                        
            elif response.status_code == 401:
                logger.error("Token expired during ID-based transaction fetch, retrying")
                retry_count += 1
                if retry_count >= max_retries:
                    logger.error("Max retries exceeded for authentication")
                    raise Exception("Max retries exceeded for authentication")
                continue
            else:
                logger.error("Failed to fetch transactions by ID. Status code: %d, Response: %s", 
                           response.status_code, response.text)
                response.raise_for_status()
            
            return checkins, biotime_checkins
            
        except requests.RequestException as e:
            retry_count += 1
            if retry_count >= max_retries:
                trace = str(e) + frappe.get_traceback(with_context=True)
                logger.error("HTTPError in ID-based fetch after %d retries: %s", max_retries, trace)
                raise e
            else:
                logger.error("ID-based request failed, retrying (%d/%d): %s", retry_count, max_retries, str(e))
                continue


def sync_all_devices_by_id() -> None:
    """
    Sync all devices using ID-based pagination instead of date ranges.
    This is the new recommended method for hourly sync.
    """
    try:
        connector_doc = frappe.get_doc("BioTime Connector", 
                                     frappe.db.get_value("BioTime Connector", {"is_enabled": 1}))
        
        last_synced_id = connector_doc.get("last_synced_id", 0)
        
        logger.error("Starting ID-based sync from ID: %d", last_synced_id)
        
        device_checkins, biotime_checkins = fetch_transactions_by_id(
            last_synced_id=last_synced_id,
            page_size=1000
        )
        
        if device_checkins or biotime_checkins:
            insert_bulk_checkins(device_checkins)
            insert_bulk_biotime_checkins(biotime_checkins)
            
            max_id = last_synced_id
            for i, checkin in enumerate(device_checkins + biotime_checkins):
                if checkin.get("transaction_id", 0) > max_id:
                    max_id = checkin["transaction_id"]
            logger.error(f"max_id calculation completed: {max_id}")
                    
            if max_id > last_synced_id:
                connector_doc.last_synced_id = max_id
                connector_doc.save(ignore_permissions=True)
                frappe.db.commit()
                
            logger.error("ID-based sync completed: %d employee checkins, %d biotime checkins, last ID: %d",
                       len(device_checkins), len(biotime_checkins), max_id)
        else:
            logger.error("No new transactions found from ID: %d", last_synced_id)            
    except Exception as e:
        logger.error("Critical error in ID-based sync: %s", str(e))
        raise e
