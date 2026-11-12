// Environment diagnostics are independent of the stimulus and assertion model.
// ENV_FATAL=0 collects recoverable errors without using the simulator's error
// counter (which may itself have a stop limit). Every error still fails the run.
integer env_fatal=1, env_failures=0, env_unrecoverable=0, env_errors_fd=0;
bit env_initialized=0, env_finished=0;

function automatic string env_json(input string value);
    string escaped;
    byte ch;
    escaped="";
    for (integer i=0; i<value.len(); i++) begin
        ch=value.getc(i);
        case (ch)
            8: escaped={escaped,"\\b"};
            9: escaped={escaped,"\\t"};
            10: escaped={escaped,"\\n"};
            12: escaped={escaped,"\\f"};
            13: escaped={escaped,"\\r"};
            34: escaped={escaped,"\\\""};
            92: escaped={escaped,"\\\\"};
            default: escaped={escaped,$sformatf("%c",ch)};
        endcase
    end
    return escaped;
endfunction

task automatic env_write_summary;
    integer summary_fd;
    begin
        summary_fd=$fopen("environment_runtime.json","w");
        if (!summary_fd) $fatal(1,"ENV_ARCHIVE_OPEN environment_runtime.json");
        $fdisplay(summary_fd,"{\"version\":1,\"env_fatal\":%0d,\"failures\":%0d,\"unrecoverable\":%0d,\"finished\":%0d}",
            env_fatal,env_failures,env_unrecoverable,env_finished);
        $fclose(summary_fd);
    end
endtask

task automatic env_open;
    begin
        if ($value$plusargs("ENV_FATAL=%d",env_fatal)) begin end
        if (!(env_fatal==0 || env_fatal==1)) $fatal(1,"ENV_CONFIGURATION_INVALID");
        env_errors_fd=$fopen("environment_errors.jsonl","w");
        if (!env_errors_fd) $fatal(1,"ENV_ARCHIVE_OPEN environment_errors.jsonl");
        env_initialized=1;
        env_write_summary();
        $display("ENV_POLICY env_fatal=%0d recoverable_errors=record_and_fail",env_fatal);
    end
endtask

task automatic env_fail(input string label, input string detail="", input bit unrecoverable=0);
    begin
        env_failures++;
        if (unrecoverable) env_unrecoverable++;
        $fdisplay(env_errors_fd,"{\"label\":\"%s\",\"time_ps\":%0.0f,\"unrecoverable\":%0d,\"detail\":\"%s\"}",
            env_json(label),$realtime*1000.0,unrecoverable,env_json(detail));
        $fflush(env_errors_fd);
        env_write_summary();
        if (unrecoverable) $fatal(1,"ENV_UNRECOVERABLE %s %s",label,detail);
        else if (env_fatal) $fatal(1,"ENV_STOP %s %s",label,detail);
        else $display("ENV_CONTINUE %s %s",label,detail);
    end
endtask

final begin
    if (env_initialized) env_write_summary();
    if (env_errors_fd) $fclose(env_errors_fd);
end
