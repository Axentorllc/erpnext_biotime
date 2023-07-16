// Copyright (c) 2023, Axentor and contributors
// For license information, please see license.txt

frappe.ui.form.on('BioTime Device', {
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
