// Copyright (c) 2023, Axentor and contributors
// For license information, please see license.txt

frappe.ui.form.on('BioTime Connector', {
	onload: function(frm) {
    frm.trigger("add_sync_devices_button");
  },
  add_sync_devices_button: function(frm) {
    frm.add_custom_button(__('Sync Devices'), function() {
            frappe.call({
                method: 'erpnext_biotime.biotime_integration.biotime_integration.fetch_and_create_devices',
                callback: function(response) {
                    if (response.message) {
                        frappe.msgprint(response.message);
                    }
                }
            });
        }, __("Manage"));
  }
});
