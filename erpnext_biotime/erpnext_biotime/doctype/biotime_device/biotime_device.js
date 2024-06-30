// Copyright (c) 2023, Axentor and contributors
// For license information, please see license.txt


frappe.ui.form.on('BioTime Device', {
  onload: function(frm) {
    frm.trigger("add_sync_device_button");
    
  },
  add_sync_device_button: function(frm) {
    frm.add_custom_button(__('Sync Today records'), function() {
            frappe.call({
                method: 'erpnext_biotime.biotime_integration.biotime_integration.fetch_and_create_devices',
                callback: function(response) {
                    if (response.message) {
                        frappe.msgprint(response.message);
                    }
                }
            });
        }, __("Device"));
      frm.add_custom_button(__('Sync Records By Date'), function() {
        var dialog = new frappe.ui.Dialog({
            title: __("Select Dates"),
            fields: [
                {
                    label: __("Start Date"),
                    fieldname: "start_date",
                    fieldtype: "Date",
                    reqd: 1
                },
                {
                    label: __("End Date"),
                    fieldname: "end_date",
                    fieldtype: "Date",
                    reqd: 1
                }
            ],
            primary_action_label: __("Fetch Transactions"),
            primary_action: function() {
                var values = dialog.get_values();
                if (!values) return;

                var start_date = values.start_date;
                var end_date = values.end_date;
                var device_id = frm.doc.device_id; 

                // Call fetch_and_create_devices function with start_date and end_date
                frappe.call({
                    method: 'erpnext_biotime.erpnext_biotime.doctype.biotime_device.biotime_device.enqueu_manual_sync',
                    args: { 
                        start_date: start_date,
                        end_date: end_date,
                        device_id:device_id
                    },
                    callback: function(response) {
                        if (response.message) {
                            frappe.msgprint(response.message);
                            frm.refresh();
                        }
                    }
                });

                dialog.hide();
            }
        });

        dialog.show();
    }, __("Device"));
  },
  device_id: function (frm) {
    let device_id = frm.doc.device_id;
    if (device_id) {
      frappe.call({
                method: 'erpnext_biotime.biotime_integration.biotime_integration.fetch_and_create_devices',
                args: {
                    device_id: device_id
                },
                callback: function(response) {
                    if (response.message) {
                        let deviceData = response.message;

                        frm.set_value('device_id', deviceData.device_id);
                        frm.set_value('device_name', deviceData.device_name);
                        frm.set_value('device_alias', deviceData.device_alias);
                        frm.set_value('device_ip_address', deviceData.device_ip_address);
                        frm.set_value('device_area', deviceData.last_activity);
                        frm.set_value('last_activity', deviceData.last_sync_request);
                        frm.set_value('last_sync_request', deviceData.device_area);
                    }
                }
            });
    }
  }
});
