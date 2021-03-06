$(document).ready(function() {
    $('.subjob_toggle').on('click', function(e) {
        var open = $(this).data("open") == "true";
        if(!open) {
            $(this).data("open", "true")
            $(this).removeClass("glyphicon-circle-arrow-down")
            $(this).addClass("glyphicon-circle-arrow-up")
            var jobid = $(this).data("jobid");
            var subjob_table = $(
                "<table class='table table-striped table-bordered subjob-table'>"+
                "</table>");
            $(this).closest("td").append(subjob_table);

            $.getJSON("/api/v1/jobs/" + jobid, function(r) {
                for(var i = 0 ; i < r["children"].length ; ++i) {
                    var child = r["children"][i];
                    $.getJSON("/api/v1/jobs/" + child["id"], function(s) {
                        var subjob_row = $(
                            "<tr>"+
                              "<td>"+
                                "<span class='glyphicon' title='failed'></span> "+
                                "<a href='/jobs/"+s["id"]+"'>"+s["title"]+"</td>"+
                              "<td>"+s["jobtype"]+"</td>"+
                            "</tr>");
                        subjob_table.append(subjob_row);
                        if(s["state"] == "queued") {
                            subjob_row.find("span.glyphicon").addClass("glyphicon-time");
                        }
                        if(s["state"] == "running") {
                            subjob_row.find("span.glyphicon").addClass("glyphicon-play").css("color", "#337AB7");
                        }
                        if(s["state"] == "failed") {
                            subjob_row.find("span.glyphicon").addClass("glyphicon-remove").css("color", "#D9534F");
                        }
                        if(s["state"] == "done") {
                            subjob_row.find("span.glyphicon").addClass("glyphicon-ok").css("color", "#5CB85C");
                        }

                        if(s["output_link"] != null) {
                            var output_link = $("<a href="+s["output_link"]+" style='margin-left:5px;'>"+
                                                  "<span class='glyphicon glyphicon-folder-open'></span>"+
                                                "</a>");
                            subjob_row.find("td:first").append(output_link);
                        }
                    });
                }
            });
        }
        else {
            $(this).data("open", "false");
            $(this).removeClass("glyphicon-circle-arrow-up");
            $(this).addClass("glyphicon-circle-arrow-down");
            $(this).closest("tr").find("table.subjob-table").remove();
        }
    });

    $('.all-jobs-selector').change(function() {
        $('input.job-selector').prop('checked', this.checked);
    });

    $('#selected-rerun').click(function() {
        if(confirm('Are you sure you want to rerun those jobs? This will include all tasks, even those already done.')) {
            var rerun_form = $("#rerun_multiple_form");
            $("input.job-selector:checked").each(function() {
                var job_input = $("<input type='hidden' name='job_id' value='"+$(this).attr('value')+"'>");
                rerun_form.append(job_input);
            });
            rerun_form.submit();
        }
    });

    $('#selected-rerun-failed').click(function() {
        if(confirm('Are you sure you want to rerun those jobs? Only the tasks that are failed will be rerun.')) {
            var rerun_form = $("#rerun_failed_multiple_form");
            $("input.job-selector:checked").each(function() {
                var job_input = $("<input type='hidden' name='job_id' value='"+$(this).attr('value')+"'>");
                rerun_form.append(job_input);
            });
            rerun_form.submit();
        }
    });

    $('#selected-pause').click(function() {
        if(confirm('Are you sure you want to pause those jobs?')) {
            var pause_form = $("#pause_multiple_form");
            $("input.job-selector:checked").each(function() {
                var job_input = $("<input type='hidden' name='job_id' value='"+$(this).attr('value')+"'>");
                pause_form.append(job_input);
            });
            pause_form.submit();
        }
    });

    $('#selected-resume').click(function() {
        if(confirm('Are you sure you want to resume those jobs?')) {
            var resume_form = $("#resume_multiple_form");
            $("input.job-selector:checked").each(function() {
                var job_input = $("<input type='hidden' name='job_id' value='"+$(this).attr('value')+"'>");
                resume_form.append(job_input);
            });
            resume_form.submit();
        }
    });

    $('#selected-delete').click(function() {
        if(confirm('Are you sure you want to delete those jobs?')) {
            var delete_form = $("#delete_multiple_form");
            $("input.job-selector:checked").each(function() {
                var job_input = $("<input type='hidden' name='job_id' value='"+$(this).attr('value')+"'>");
                delete_form.append(job_input);
            });
            delete_form.submit();
        }
    });

    $('#selected-move').click(function() {
        var move_form = $("#move-multiple-form");
        move_form.children("input[name='job_id']").remove();
        $("input.job-selector:checked").each(function() {
                var job_input = $("<input type='hidden' name='job_id' value='"+$(this).attr('value')+"'>");
                move_form.append(job_input);
        });
        $('#selected-move-modal').modal('toggle');
    });

    $("#move-multiple-submit").click(function() {
        $("#move-multiple-form").submit();
    });

    $('#selected-set-prio-weight').click(function() {
        var form = $("#set-prio-weight-multiple-form");
        form.children("input[name='job_id']").remove();
        $("input.job-selector:checked").each(function() {
                var job_input = $("<input type='hidden' name='job_id' value='"+$(this).attr('value')+"'>");
                form.append(job_input);
        });
        $('#selected-set-prio-weight-modal').modal('toggle');
    });

    $("#set-prio-weight-submit").click(function() {
        $("#set-prio-weight-multiple-form").submit();
    });

    $('#selected-add-tag').click(function() {
        var form = $("#add-tag-multiple-form");
        form.children("input[name='job_id']").remove();
        $("input.job-selector:checked").each(function() {
                var job_input = $("<input type='hidden' name='job_id' value='"+$(this).attr('value')+"'>");
                form.append(job_input);
        });
        $('#selected-add-tag-modal').modal('toggle');
    });

    $("#add-tag-submit").click(function() {
        $("#add-tag-multiple-form").submit();
    });

    $('#selected-remove-tag').click(function() {
        var form = $("#remove-tag-multiple-form");
        form.children("input[name='job_id']").remove();
        $("input.job-selector:checked").each(function() {
                var job_input = $("<input type='hidden' name='job_id' value='"+$(this).attr('value')+"'>");
                form.append(job_input);
        });
        $('#selected-remove-tag-modal').modal('toggle');
    });

    $("#remove-tag-submit").click(function() {
        $("#remove-tag-multiple-form").submit();
    });

    $('#selected-add-tag-requirement').click(function() {
        var form = $("#add-tag-requirement-multiple-form");
        form.children("input[name='job_id']").remove();
        $("input.job-selector:checked").each(function() {
                var job_input = $("<input type='hidden' name='job_id' value='"+$(this).attr('value')+"'>");
                form.append(job_input);
        });
        $('#selected-add-tag-requirement-modal').modal('toggle');
    });

    $("#add-tag-requirement-submit").click(function() {
        $("#add-tag-requirement-multiple-form").submit();
    });

    $('#selected-remove-tag-requirement').click(function() {
        var form = $("#remove-tag-requirement-multiple-form");
        form.children("input[name='job_id']").remove();
        $("input.job-selector:checked").each(function() {
                var job_input = $("<input type='hidden' name='job_id' value='"+$(this).attr('value')+"'>");
                form.append(job_input);
        });
        $('#selected-remove-tag-requirement-modal').modal('toggle');
    });

    $("#remove-tag-requirement-submit").click(function() {
        $("#remove-tag-requirement-multiple-form").submit();
    });
});
