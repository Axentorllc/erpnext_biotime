frappe.listview_settings['BioTime Device'] = {
    onload: function(listview) {
        listview.page.add_inner_button(__('Sync Records'), function() {
            let dialog = new frappe.ui.Dialog({
                title: __("Select Dates"),
                fields: [
                    {
                        label: __("Start Time"),
                        fieldname: "start_time",
                        fieldtype: "Datetime",
                        reqd: 1
                    },
                    {
                        label: __("End Time"),
                        fieldname: "end_time",
                        fieldtype: "Datetime",
                        reqd:1
                    
                    },
                    {
                        label: __("Employee"),
                        fieldname: "emp_code",
                        fieldtype: "Link",
                        options:"Employee"
                    }
                ],
                primary_action_label: __("Fetch Transactions"),
                primary_action: function(data) {
                    if (!data) return;

                    // Convert to JavaScript Date objects for comparison
                    let startDate = new Date(data.start_time);
                    let endDate = new Date(data.end_time);

                    // Validate date range
                    if (endDate < startDate) {
                        frappe.msgprint({
                            title: __("Invalid Date Range"),
                            message: __("End Time must be greater than Start Time."),
                            indicator: "red"
                        });
                        return;  // Stop execution
                    }


                    frappe.call({
                        method: 'erpnext_biotime.erpnext_biotime.doctype.biotime_device.biotime_device.enqueu_all_sync',
                        args: {
                            start_time: data.start_time,
                            end_time: data.end_time || null,
                            emp_code: data.emp_code || null

                        },
                        callback: function(response) {
                            if (response.message) {
                                frappe.msgprint(response.message);
                            }
                        }
                    });

                    dialog.hide();
                }
            });

            dialog.show();
        });
    }
};
