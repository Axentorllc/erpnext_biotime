# Copyright (c) 2023, Axentor and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from erpnext_biotime.biotime_integration.biotime_integration import insert_bulk_biotime_checkins
from erpnext_biotime.biotime_integration.biotime_integration import fetch_transactions
from erpnext_biotime.biotime_integration.biotime_integration import insert_bulk_checkins

logger = frappe.logger("biotime", allow_site=True, file_count=50)
class BioTimeDevice(Document):
    pass

def manual_sync_transactions_by_date_range(start_date, end_date, device_id) -> None:
    page_size = 1000
    terminal_alias = frappe.db.get_value("BioTime Device", {"device_id": device_id}, "device_alias")

    if not terminal_alias:
        return f"Device ID {device_id} has no device_alias "

    all_checkins = []
    all_biotime_checkins = []

    device_checkins, biotime_checkins = fetch_transactions(
        start_time=start_date, end_time=end_date, terminal_alias=terminal_alias, page_size=page_size
    )
    
    
    if not (start_date and end_date and start_date <= end_date) or not device_checkins:
        frappe.msgprint("Please ensure you provide a valid date range.")

    logger.error(f"Manual Fetching: Number of check-ins in Device ID {device_id}: %s", len(device_checkins))
    
    all_checkins.extend(device_checkins)
    all_biotime_checkins.extend(biotime_checkins)

    insert_bulk_checkins(all_checkins)
    insert_bulk_biotime_checkins(all_biotime_checkins)


def manual_sync_all_transactions(start_time,end_time,emp_code=None) -> None:
    page_size=1000
 
    try:

        device_checkins, biotime_checkins = fetch_transactions(
            start_time=start_time, end_time=end_time,emp_code=emp_code,page_size=page_size)

        logger.error(f"Synced {len(device_checkins)} checkins from {start_time} to {end_time}") 
        
        insert_bulk_checkins(device_checkins)
        insert_bulk_biotime_checkins(biotime_checkins)

    except Exception as e:
        logger.error(f"Error syncing transactions: {str(e)}")


@frappe.whitelist()
def enqueu_manual_sync(start_date, end_date, device_id):
    frappe.enqueue(
        manual_sync_transactions_by_date_range,
        queue="long",
        job_name="Manual Biotime Device Sync",
        start_date=start_date,
        end_date=end_date,
        device_id=device_id,
    )

    frappe.msgprint("Syncing the transactions in processing; It may take a few seconds.")

@frappe.whitelist()
def enqueu_all_sync(start_time, end_time, emp_code=None):
    frappe.enqueue(manual_sync_all_transactions,
        queue="long",
        job_name="Manual Full Sync",
        start_time=start_time,   
        end_time=end_time,       
        emp_code=emp_code  )
    
    frappe.msgprint("Syncing the transactions in processing; It may take a few seconds.")