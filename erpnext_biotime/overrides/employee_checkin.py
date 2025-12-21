
# Copyright (c) 2025, Axentor, LLC. and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, get_datetime
from datetime import datetime, timedelta
from itertools import groupby

# from hrms.hr.doctype.attendance.attendance import mark_attendance
# from hrms.hr.doctype.employee_checkin.employee_checkin import EmployeeCheckin as BaseEmployeeCheckin
from hrms.hr.doctype.employee_checkin.employee_checkin import handle_attendance_exception

def on_update(doc, event):
	if not cint(frappe.get_value("BioTime Settings", "BioTime Settings", "autoupdate_attendance")) or not doc.get('shift'):
		return

	shift_name = doc.shift
	shift_doc = frappe.get_doc("Shift Type", shift_name)
	create_or_update_attendance_for_employee_checkin(doc, shift_doc)

def create_or_update_attendance_for_employee_checkin(checkin, shift_doc):
	"""Creates or Updates Attendance for the given Employee Checkin based on the Shift Type.
	:param doc: The Employee Checkin Document.
	:param shift_doc: The Shift Type Document.
	"""
	# Fetch all logs for the employee on the attendance date and shift
	logs = frappe.get_all(
		"Employee Checkin",
		filters={
			"employee": checkin.employee,
			"shift": checkin.shift,
			"shift_actual_start": checkin.shift_actual_start,
			"offshift": 0,
		},
		fields=["name",
				"employee",
				"log_type",
				"time",
				"shift",
				"shift_start",
				"shift_end",
				"shift_actual_start",
				"shift_actual_end",
				"device_id"],
		order_by="time",
	)

	attendance_date = checkin.shift_actual_start.date()

	attendance_status, total_working_hours, late_entry, early_exit, in_time, out_time = shift_doc.get_attendance(logs)
	mark_attendance_and_link_log(
		logs,
		attendance_status,
		attendance_date=attendance_date,
		working_hours=total_working_hours,
		late_entry=late_entry,
		early_exit=early_exit,
		in_time=in_time,
		out_time=out_time,
		shift=checkin.shift,
	)

def get_employee_checkins(shift) -> list[dict]:
		return frappe.get_all(
			"Employee Checkin",
			fields=[
				"name",
				"employee",
				"log_type",
				"time",
				"shift",
				"shift_start",
				"shift_end",
				"shift_actual_start",
				"shift_actual_end",
				"device_id",
			],
			filters={
				"shift":shift,
				"offshift": 0,
			},
			order_by="employee,time",
		)

def mark_attendance_and_link_log(
	logs,
	attendance_status,
	attendance_date=None,
	working_hours=None,
	late_entry=False,
	early_exit=False,
	in_time=None,
	out_time=None,
	shift=None,
):
	"""Creates an attendance and links the attendance to the Employee Checkin.

	:param logs: The List of 'Employee Checkin'.
	:param attendance_status: Attendance status to be marked. One of: (Present, Absent, Half Day, Skip). Note: 'On Leave' is not supported by this function.
	:param attendance_date: Date of the attendance to be created.
	:param working_hours: (optional)Number of working hours for the given date.
	"""
	log_names = [x.name for x in logs]
	employee = logs[0].employee

	# if attendance_status == "Skip":
	# 	skip_attendance_in_checkins(log_names)
	# 	return None

	if attendance_status in ("Present", "Absent", "Half Day"):
		try:
			frappe.db.savepoint("attendance_creation")
			if attendance := get_existing_half_day_attendance(employee, attendance_date):
				frappe.db.set_value(
					"Attendance",
					attendance.name,
					{
						"working_hours": working_hours,
						"shift": shift,
						"late_entry": late_entry,
						"early_exit": early_exit,
						"in_time": in_time,
						"out_time": out_time,
						"modify_half_day_status": 0,
						"status": attendance_status
					},
				)
			else:
				attendance = frappe.new_doc("Attendance")
				attendance.update(
					{
						"doctype": "Attendance",
						"employee": employee,
						"status": attendance_status,
						"working_hours": working_hours,
						"attendance_date": attendance_date,
						"shift": shift,
						"late_entry": late_entry,
						"early_exit": early_exit,
						"in_time": in_time,
						"out_time": out_time
					}
				).submit()


			update_attendance_in_checkins(log_names, attendance.name)
			return attendance

		except frappe.ValidationError as e:
			handle_attendance_exception(log_names, e)

	else:
		frappe.throw(_("{} is an invalid Attendance Status.").format(attendance_status))

def get_existing_half_day_attendance(employee, attendance_date=None):
	attendance_name = frappe.db.exists(
		"Attendance",
		{
			"employee": employee,
			"attendance_date":  attendance_date,
		},
	)

	if attendance_name:
		attendance_doc = frappe.get_doc("Attendance", attendance_name)
		return attendance_doc
	return None

def update_attendance_in_checkins(log_names: list, attendance_id: str):
	EmployeeCheckin = frappe.qb.DocType("Employee Checkin")
	(
		frappe.qb.update(EmployeeCheckin)
		.set("attendance", attendance_id)
		.where(EmployeeCheckin.name.isin(log_names))
	).run()