// main_phase drives once AFTER each posedge. A separate posedge sampler checks
// what the DUT actually consumed. The initial edge is an explicit reset bootstrap.
`include "main_phase_base.svh"
`include "sv_tc_factory.svh"
reg sv_tc_mode=0, main_clock_start=0, main_clock_stop=0, main_have_inputs=0;
integer sv_frames=0, sv_body_slots=0, sv_active=0;
integer main_samples, main_assignments, main_stage=0;
integer main_low_ns=5, main_high_ns=5, main_pause_ns=0, main_current_high_ns=5;
longint main_edge_ps=0;
string main_name;
cmdbuf_main_tc main_tc;

initial begin : main_clock
    integer lo,hi,pause_ns;
    wait(main_clock_start);
    while (!main_clock_stop) begin
        lo=main_low_ns; hi=main_high_ns; pause_ns=main_pause_ns;
        #(lo+pause_ns);
        if (!main_clock_stop) begin
            main_current_high_ns=hi;
            clk=1;
            #(hi); clk=0;
        end
    end
end

task automatic main_fail(input string reason);
    begin
        $display("MAIN_PHASE_CONTEXT case=%s cycle=%0d edge=%0d stage=%0d phase=%0d rst_n=%b rready=%b arready=%b req=%h arb_addr=%h mem_due=%b due_data=%h due_error=%b",
            main_name,main_tc.cycle,cycle,main_stage,main_tc.phase,main_tc.rst_n,main_tc.rready,
            arready,req,arb_addr,main_tc.mem_due,main_tc.due_data,main_tc.due_error);
        $display("MAIN_PROPOSED_INPUT arvalid=%b src=%b arresp=%b araddr=%h arid=%h port=%h argrant=%h rwgrant=%h status=%p mem_rdata=%h pc_err=%b",
            main_tc.arvalid,main_tc.src,main_tc.arresp,main_tc.araddr,main_tc.arid,main_tc.ar_port_id,
            main_tc.ar_rd_gnt,main_tc.rw_rd_gnt,main_tc.rd_mem_status,main_tc.mem_rdata,main_tc.pc_err);
        $fatal(1,"%s case=%s cycle=%0d edge=%0d",reason,main_name,main_tc.cycle,cycle);
    end
endtask

task automatic main_begin(input cmdbuf_main_tc t);
    begin
        if (main_have_inputs || main_stage!=0 || t.cycle!=sv_frames ||
            $realtime*1000.0 != main_edge_ps+1000) main_fail("MAIN_ONCE_AFTER_POSEDGE");
        main_stage=1;
    end
endtask

task automatic main_control(input cmdbuf_main_tc t);
    logic previous_reset;
    begin
        if (main_stage!=1) main_fail("MAIN_CONTROL_ORDER");
        if ($isunknown({t.rst_n,t.rready,t.clk_low_ns,t.clk_high_ns,t.clk_pause_ns}) ||
            t.clk_low_ns<3 || t.clk_high_ns<3 || t.clk_low_ns>1000000 ||
            t.clk_high_ns>1000000 || t.clk_pause_ns>1000000) main_fail("MAIN_CONTROL_INVALID");
        previous_reset=rst_n;
        rst_n=t.rst_n; rready=t.rready;
        main_low_ns=t.clk_low_ns; main_high_ns=t.clk_high_ns; main_pause_ns=t.clk_pause_ns;
        if (!rst_n && previous_reset !== 1'b0) begin
            wave_resets++;
            wave_aborted=accepted-retired;
            wave_aborted_unissued=accepted-issued;
            wave_aborted_mem=issued-captured;
            wave_aborted_response=captured-retired;
            due_data.delete(); due_parity.delete();
            $fdisplay(events,"%0d,RESET,-1,0,0,0,0,%0d",cycle,wave_aborted);
        end
        main_stage=2;
    end
endtask

task automatic main_context(input cmdbuf_main_tc t);
    begin
        if (main_stage!=2 || $realtime*1000.0 != main_edge_ps+2000) main_fail("MAIN_CONTEXT_TIMING");
        if ($isunknown({arready,req}) || ((|req) && $isunknown(arb_addr))) main_fail("MAIN_UNKNOWN_CONTEXT");
        t.edge_count=cycle; t.arready=arready; t.req=req; t.arb_addr=arb_addr;
        t.mem_due=due_data.exists(cycle);
        t.due_data=due_data.exists(cycle) ? due_data[cycle] : 0;
        t.due_error=due_parity.exists(cycle) ? due_parity[cycle] : 0;
        main_stage=3;
    end
endtask

task automatic main_record_assignment(input cmdbuf_main_tc t);
    $fdisplay(main_assignments,"%0d,%0d,%0d,%0.0f,%0d,%0d,%b,%0d,%0d,%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%h,%h,%h,%032h,%0d",
        t.cycle,cycle,t.phase,$realtime*1000.0,main_edge_ps,main_edge_ps+1000*(main_current_high_ns+t.clk_low_ns+t.clk_pause_ns),1'b1,
        t.clk_low_ns,t.clk_high_ns,t.clk_pause_ns,t.rst_n,t.arvalid,t.src,t.araddr,
        t.arid,t.ar_port_id,t.arresp,t.rready,t.ar_rd_gnt,t.rw_rd_gnt,t.rd_mem_status[0],
        t.rd_mem_status[1],t.rd_mem_status[2],t.rd_mem_status[3],t.rd_mem_status[4],t.rd_mem_status[5],t.rd_mem_status[6],t.rd_mem_status[7],
        t.mem_rdata,t.pc_err,t.arready,t.req,t.arb_addr,t.issue_data,t.issue_error);
endtask

task automatic main_record_sample;
    $fdisplay(main_samples,"%0d,%0d,%0d,%0.0f,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%h,%h,%h,%h,%h,%h,%h,%h,%h,%h",
        main_tc.cycle,cycle,main_tc.phase,$realtime*1000.0,clk,rst_n,arvalid,
        src,araddr,arid,ar_port_id,arresp,rready,ar_rd_gnt,
        rw_rd_gnt,rd_mem_status[0],rd_mem_status[1],rd_mem_status[2],rd_mem_status[3],rd_mem_status[4],rd_mem_status[5],
        rd_mem_status[6],rd_mem_status[7],mem_rdata,pc_err,arready,req,arb_addr,
        pending_cnt,rvalid,rdata,rid,r_port_id,raddr,rresp);
endtask

task automatic main_drive(input cmdbuf_main_tc t);
    begin
        if (main_stage!=3 || $realtime*1000.0 != main_edge_ps+2000) main_fail("MAIN_DRIVE_ORDER");
        // Validate, never repair. Legal values are assigned by the TC's if blocks.
        if ($isunknown({t.rst_n,t.rready,t.arvalid,t.src,t.araddr,t.arid,t.ar_port_id,
            t.arresp,t.ar_rd_gnt,t.rw_rd_gnt,t.mem_rdata,t.pc_err,t.issue_data,t.issue_error})) main_fail("MAIN_P_KNOWN");
        for (integer b=0;b<8;b++) if ($isunknown(t.rd_mem_status[b])) main_fail("MAIN_P_KNOWN");
        if (t.rst_n) begin
            if (t.arvalid && t.src>2) main_fail("MAIN_E03_SRC");
            if (t.arvalid && t.arresp!=0 && t.arresp!=3) main_fail("MAIN_E04_ARRESP");
            if (t.arready==0 && t.arvalid) main_fail("MAIN_E05_ZERO_READY");
            for (integer b=0;b<8;b++)
                if (t.req[b] && t.ar_rd_gnt[b] && t.rw_rd_gnt[b] &&
                    t.rd_mem_status[b][t.arb_addr[0]]!==1'b1) main_fail("MAIN_E06_SUCCESS_BUSY");
            if (t.mem_due && {t.mem_rdata,t.pc_err} !== {t.due_data,t.due_error}) main_fail("MAIN_T_MEM4");
        end
        if ({arready,req,arb_addr} !== {t.arready,t.req,t.arb_addr}) main_fail("MAIN_CONTEXT_CHANGED");
        main_record_assignment(t);
        arvalid=t.arvalid; src=t.src; araddr=t.araddr; arid=t.arid;
        ar_port_id=t.ar_port_id; arresp=t.arresp;
        ar_rd_gnt=t.ar_rd_gnt; rw_rd_gnt=t.rw_rd_gnt;
        for (integer b=0;b<8;b++) rd_mem_status[b]=t.rd_mem_status[b];
        mem_rdata=t.mem_rdata; pc_err=t.pc_err;
        sv_frames++;
        if (t.phase==0) sv_body_slots++;
        main_have_inputs=1; main_stage=0;
        #0.001;
        if (inject=="main_post") arid=arid ^ 8'h01;
        if ({rst_n,rready,arvalid,src,araddr,arid,ar_port_id,arresp,ar_rd_gnt,rw_rd_gnt,mem_rdata,pc_err} !==
            {t.rst_n,t.rready,t.arvalid,t.src,t.araddr,t.arid,t.ar_port_id,t.arresp,t.ar_rd_gnt,t.rw_rd_gnt,t.mem_rdata,t.pc_err})
            begin
                $display("MAIN_PIN_DETAIL actual=%h expected=%h",
                    {rst_n,rready,arvalid,src,araddr,arid,ar_port_id,arresp,ar_rd_gnt,rw_rd_gnt,mem_rdata,pc_err},
                    {t.rst_n,t.rready,t.arvalid,t.src,t.araddr,t.arid,t.ar_port_id,t.arresp,t.ar_rd_gnt,t.rw_rd_gnt,t.mem_rdata,t.pc_err});
                main_fail("MAIN_PIN_MODIFICATION");
            end
        for (integer b=0;b<8;b++)
            if (rd_mem_status[b] !== t.rd_mem_status[b]) begin
                $display("MAIN_PIN_DETAIL bank=%0d actual=%b expected=%b",b,rd_mem_status[b],t.rd_mem_status[b]);
                main_fail("MAIN_PIN_MODIFICATION");
            end
        if ({arready,req,arb_addr} !== {t.arready,t.req,t.arb_addr}) main_fail("MAIN_CONTEXT_CHANGED");
    end
endtask

// This observer is an independent posedge process. It never drives any input.
always @(posedge clk) if (sv_tc_mode && main_clock_start) begin
    main_edge_ps=$realtime*1000.0;
    if (main_have_inputs) begin
        main_record_sample();
        main_have_inputs=0;
        if (rst_n) begin
            sv_active++;
            observe_active(main_tc.issue_error,main_tc.issue_data);
        end else begin #1; cycle++; end
    end else if (cycle==0 && rst_n===1'b0) begin
        $display("MAIN_BOOTSTRAP time_ps=%0d rst_n=0",main_edge_ps);
        #0.001; cycle++;
    end else main_fail("MAIN_MISSING_ASSIGNMENT");
end

task automatic main_tail(input integer tail_phase);
    begin
        main_tc.begin_cycle(sv_frames,tail_phase);
        main_tc.rst_n=1; main_tc.rready=7;
        main_tc.clk_low_ns=5; main_tc.clk_high_ns=5; main_tc.clk_pause_ns=0;
        main_tc.drive_control();
        #1; main_tc.sample_context();
        main_tc.arvalid=0; main_tc.src=0; main_tc.arresp=0;
        main_tc.araddr=0; main_tc.arid=0; main_tc.ar_port_id=0;
        main_tc.ar_rd_gnt=255; main_tc.rw_rd_gnt=255;
        for (integer b=0;b<8;b++) begin
            if (main_tc.req[b]) main_tc.rd_mem_status[b]=(2'b01 << main_tc.arb_addr[0]);
            else main_tc.rd_mem_status[b]=0;
        end
        main_tc.issue_data=memory_pattern(issued,arb_addr); main_tc.issue_error=0;
        if (main_tc.mem_due) begin main_tc.mem_rdata=main_tc.due_data; main_tc.pc_err=main_tc.due_error; end
        else begin main_tc.mem_rdata=0; main_tc.pc_err=0; end
        main_tc.drive_inputs();
    end
endtask

task automatic run_sv_tc;
    string magic;
    integer override_cycles, header_seed, config_fd;
    process rng;
    begin
        if ($fscanf(fd,"%s %s %d %d\n",magic,main_name,header_seed,override_cycles)!=4 ||
            magic!="MAIN_PHASE_V1" || header_seed!=seed) $fatal(1,"MAIN_BAD_PROGRAM");
        $fclose(fd);
        main_tc=create_sv_tc(main_name);
        if (main_tc==null) $fatal(1,"MAIN_UNKNOWN_NAME %s",main_name);
        if (override_cycles!=0) main_tc.num_cycles=override_cycles;
        if (main_tc.num_cycles<1 || main_tc.num_cycles>1000000) main_fail("MAIN_CYCLES_INVALID");
        rng=process::self(); rng.srandom(seed);
        config_fd=$fopen("tc_config.json","w");
        $fdisplay(config_fd,"{\"name\":\"%s\",\"seed\":%0d,\"num_cycles\":%0d,\"bootstrap_edges\":1}",main_name,seed,main_tc.num_cycles);
        $fclose(config_fd);
        main_samples=$fopen("samples.csv","w"); main_assignments=$fopen("assigned.csv","w");
        if (!main_samples || !main_assignments) $fatal(1,"MAIN_ARCHIVE_OPEN");
        $fdisplay(main_assignments,"cycle,edge,phase,time_ps,launch_ps,expected_sample_ps,clk,clk_low_ns,clk_high_ns,clk_pause_ns,rst_n,arvalid,src,araddr,arid,ar_port_id,arresp,rready,ar_rd_gnt,rw_rd_gnt,rd_mem_status[0],rd_mem_status[1],rd_mem_status[2],rd_mem_status[3],rd_mem_status[4],rd_mem_status[5],rd_mem_status[6],rd_mem_status[7],mem_rdata,pc_err,arready,req,arb_addr,issue_data,issue_error");
        $fdisplay(main_samples,"cycle,edge,phase,time_ps,clk,rst_n,arvalid,src,araddr,arid,ar_port_id,arresp,rready,ar_rd_gnt,rw_rd_gnt,rd_mem_status[0],rd_mem_status[1],rd_mem_status[2],rd_mem_status[3],rd_mem_status[4],rd_mem_status[5],rd_mem_status[6],rd_mem_status[7],mem_rdata,pc_err,arready,req,arb_addr,pending_cnt,rvalid,rdata,rid,r_port_id,raddr,rresp");
        // Establish a known reset before the first edge awaited by main_phase.
        rst_n=0; rready=0; arvalid=0; src=0; arresp=0; araddr=0; arid=0; ar_port_id=0;
        ar_rd_gnt=0; rw_rd_gnt=0; mem_rdata=0; pc_err=0;
        for (integer b=0;b<8;b++) rd_mem_status[b]=0;
        wave_resets=1; main_clock_start=1;
        main_tc.main_phase();
        if (sv_body_slots!=main_tc.num_cycles) main_fail("MAIN_BODY_COUNT");
        // Consume the final body assignment before examining the drain condition.
        @(posedge clk); #1;
        for (integer k=0;k<drain_limit &&
             (accepted!=retired+wave_aborted || due_data.num()!=0 || req!=0 || pending_cnt!=0);k++) begin
            main_tail(1);
            @(posedge clk); #1;
        end
        if (accepted!=retired+wave_aborted || accepted!=issued+wave_aborted_unissued ||
            issued!=captured+wave_aborted_mem || captured!=retired+wave_aborted_response ||
            due_data.num()!=0 || req!=0 || pending_cnt!=0) main_fail("MAIN_DRAIN_FAILED");
        repeat (8) begin
            main_tail(2);
            @(posedge clk); #1;
        end
        #0.001; main_clock_stop=1;
        $fclose(main_samples); $fclose(main_assignments);
        $display("MAIN_PHASE_DONE case=%s assignments=%0d edges=%0d active_edges=%0d resets=%0d cancelled=%0d",
            main_name,sv_frames,cycle,sv_active,wave_resets,wave_aborted);
    end
endtask
