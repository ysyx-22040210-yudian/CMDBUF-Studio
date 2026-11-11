// SV TC execution: final solver values are copied once, then independently sampled.
`include "sv_tc_base.svh"
`include "sv_tc_factory.svh"
reg sv_tc_mode = 0;
cmdbuf_sv_tc tc;
integer sv_samples, sv_solutions, sv_frames=0, sv_active=0, sv_body_slots=0, sv_input_solves=0;
string sv_name;

 task automatic sv_fail(input string reason);
    begin
        $display("SV_TC_CONTEXT case=%s cycle=%0d edge=%0d stage=%0d phase=%0d rst_n=%b rready=%b arready=%b req=%h arb_addr=%h mem_due=%b due_data=%h due_error=%b",
            sv_name,tc.cycle,tc.edge_count,tc.stage,tc.phase,tc.rst_n,tc.rready,tc.arready,
            tc.req,tc.arb_addr,tc.mem_due,tc.due_data,tc.due_error);
        $fatal(1,"%s case=%s cycle=%0d edge=%0d",reason,sv_name,tc.cycle,tc.edge_count);
    end
 endtask

task automatic sv_record_solution;
    $fdisplay(sv_solutions,"%0d,%0d,%0d,%0.0f,%b,%0d,%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%h,%h,%h,%032h,%0d",
        tc.cycle,cycle,tc.phase,$realtime*1000.0,tc.clk_enable,tc.clk_low_ns,tc.clk_high_ns,
        tc.rst_n,tc.arvalid,tc.src,tc.araddr,tc.arid,tc.ar_port_id,tc.arresp,
        tc.rready,tc.ar_rd_gnt,tc.rw_rd_gnt,tc.rd_mem_status[0],tc.rd_mem_status[1],tc.rd_mem_status[2],tc.rd_mem_status[3],
        tc.rd_mem_status[4],tc.rd_mem_status[5],tc.rd_mem_status[6],tc.rd_mem_status[7],tc.mem_rdata,tc.pc_err,tc.arready,
        tc.req,tc.arb_addr,tc.issue_data,tc.issue_error);
endtask

task automatic sv_record_sample;
    $fdisplay(sv_samples,"%0d,%0d,%0d,%0.0f,%b,%0d,%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%h,%h,%h,%h,%h,%h,%h,%h,%h,%h",
        tc.cycle,cycle,tc.phase,$realtime*1000.0,clk,tc.clk_low_ns,tc.clk_high_ns,
        rst_n,arvalid,src,araddr,arid,ar_port_id,arresp,
        rready,ar_rd_gnt,rw_rd_gnt,rd_mem_status[0],rd_mem_status[1],rd_mem_status[2],rd_mem_status[3],
        rd_mem_status[4],rd_mem_status[5],rd_mem_status[6],rd_mem_status[7],mem_rdata,pc_err,arready,
        req,arb_addr,pending_cnt,rvalid,rdata,rid,r_port_id,
        raddr,rresp);
endtask

 task automatic sv_step(input integer phase);
    logic old_reset;
    begin
        tc.cycle=sv_frames; tc.edge_count=cycle; tc.phase=phase; tc.stage=0;
        if (!tc.randomize(rst_n,rready,clk_enable,clk_low_ns,clk_high_ns))
            sv_fail("SV_TC_CONTROL_UNSAT");
        old_reset=rst_n;
        rst_n=tc.rst_n; rready=tc.rready;
        if (!rst_n && old_reset !== 1'b0) begin
            wave_resets++;
            wave_aborted=accepted-retired;
            wave_aborted_unissued=accepted-issued;
            wave_aborted_mem=issued-captured;
            wave_aborted_response=captured-retired;
            due_data.delete(); due_parity.delete();
            $fdisplay(events,"%0d,RESET,-1,0,0,0,0,%0d",cycle,wave_aborted);
        end
        #1; // Let async reset and rready -> arready settle before sampling context.
        if ($isunknown({arready,req}) || ((|req) && $isunknown(arb_addr)))
            sv_fail("SV_TC_UNKNOWN_CONTEXT");
        tc.arready=arready; tc.req=req; tc.arb_addr=arb_addr;
        tc.mem_due=due_data.exists(cycle);
        tc.due_data=due_data.exists(cycle) ? due_data[cycle] : 0;
        tc.due_error=due_parity.exists(cycle) ? due_parity[cycle] : 0;
        tc.stage=1;
        tc.rst_n.rand_mode(0); tc.rready.rand_mode(0); tc.clk_enable.rand_mode(0);
        tc.clk_low_ns.rand_mode(0); tc.clk_high_ns.rand_mode(0);
        if (inject=="sv_unsat") begin
            if (!tc.randomize() with { arvalid == 0; arvalid == 1; })
                sv_fail("SV_TC_INPUT_UNSAT");
        end else if (!tc.randomize()) sv_fail("SV_TC_INPUT_UNSAT");
        sv_input_solves++;
        if (phase==0) sv_body_slots++;
        sv_record_solution();
        arvalid=tc.arvalid; src=tc.src; araddr=tc.araddr; arid=tc.arid;
        ar_port_id=tc.ar_port_id; arresp=tc.arresp;
        ar_rd_gnt=tc.ar_rd_gnt; rw_rd_gnt=tc.rw_rd_gnt;
        for (integer b=0;b<8;b++) rd_mem_status[b]=tc.rd_mem_status[b];
        mem_rdata=tc.mem_rdata; pc_err=tc.pc_err;
        if (inject=="sv_post") arid=arid ^ 8'h01;
        #1;
        if ({rst_n,rready,arvalid,src,araddr,arid,ar_port_id,arresp,ar_rd_gnt,rw_rd_gnt,mem_rdata,pc_err} !==
            {tc.rst_n,tc.rready,tc.arvalid,tc.src,tc.araddr,tc.arid,tc.ar_port_id,tc.arresp,tc.ar_rd_gnt,tc.rw_rd_gnt,tc.mem_rdata,tc.pc_err})
            sv_fail("SV_TC_POST_SOLVE_MODIFICATION");
        for (integer b=0;b<8;b++)
            if (rd_mem_status[b] !== tc.rd_mem_status[b]) sv_fail("SV_TC_POST_SOLVE_MODIFICATION");
        if ({arready,req,arb_addr} !== {tc.arready,tc.req,tc.arb_addr})
            sv_fail("SV_TC_CONTEXT_CHANGED");
        #(tc.clk_low_ns-2);
        clk=tc.clk_enable;
        sv_record_sample();
        if (tc.clk_enable) begin
            if (rst_n) begin
                sv_active++;
                observe_active(tc.issue_error,tc.issue_data); // Pre-NBA capture, then #1.
            end else begin #1; cycle++; end
        end else #1;
        #(tc.clk_high_ns-1);
        clk=0;
        tc.rst_n.rand_mode(1); tc.rready.rand_mode(1); tc.clk_enable.rand_mode(1);
        tc.clk_low_ns.rand_mode(1); tc.clk_high_ns.rand_mode(1);
        sv_frames++;
    end
 endtask

 task automatic run_sv_tc;
    string magic;
    integer override_cycles, header_seed, total, config_fd;
    begin
        if ($fscanf(fd,"%s %s %d %d\n",magic,sv_name,header_seed,override_cycles)!=4 ||
            magic!="SV_TC_V1" || header_seed!=seed) $fatal(1,"SV_TC_BAD_PROGRAM");
        $fclose(fd);
        tc=create_sv_tc(sv_name);
        if (tc==null) $fatal(1,"SV_TC_UNKNOWN_NAME %s",sv_name);
        tc.srandom(seed); tc.stage=2; tc.phase=0; tc.cycle=0; tc.edge_count=0;
        if (override_cycles!=0) begin
            if (!tc.randomize() with { num_cycles == local::override_cycles; }) sv_fail("SV_TC_CONFIG_UNSAT");
        end else if (!tc.randomize()) sv_fail("SV_TC_CONFIG_UNSAT");
        total=tc.num_cycles; tc.num_cycles.rand_mode(0);
        config_fd=$fopen("tc_config.json","w");
        $fdisplay(config_fd,"{\"name\":\"%s\",\"seed\":%0d,\"num_cycles\":%0d}",sv_name,seed,total);
        $fclose(config_fd);
        sv_samples=$fopen("samples.csv","w"); sv_solutions=$fopen("sv_solutions.csv","w");
        if (!sv_samples || !sv_solutions) $fatal(1,"SV_TC_ARCHIVE_OPEN");
        $fdisplay(sv_solutions,"cycle,edge,phase,time_ps,clk,clk_low_ns,clk_high_ns,rst_n,arvalid,src,araddr,arid,ar_port_id,arresp,rready,ar_rd_gnt,rw_rd_gnt,rd_mem_status[0],rd_mem_status[1],rd_mem_status[2],rd_mem_status[3],rd_mem_status[4],rd_mem_status[5],rd_mem_status[6],rd_mem_status[7],mem_rdata,pc_err,arready,req,arb_addr,issue_data,issue_error");
        $fdisplay(sv_samples,"cycle,edge,phase,time_ps,clk,clk_low_ns,clk_high_ns,rst_n,arvalid,src,araddr,arid,ar_port_id,arresp,rready,ar_rd_gnt,rw_rd_gnt,rd_mem_status[0],rd_mem_status[1],rd_mem_status[2],rd_mem_status[3],rd_mem_status[4],rd_mem_status[5],rd_mem_status[6],rd_mem_status[7],mem_rdata,pc_err,arready,req,arb_addr,pending_cnt,rvalid,rdata,rid,r_port_id,raddr,rresp");
        for (integer k=0;k<total;k++) sv_step(0);
        for (integer k=0;k<drain_limit &&
             (accepted!=retired+wave_aborted || due_data.num()!=0 || req!=0 || pending_cnt!=0);k++) sv_step(1);
        if (accepted!=retired+wave_aborted || accepted!=issued+wave_aborted_unissued ||
            issued!=captured+wave_aborted_mem || captured!=retired+wave_aborted_response ||
            due_data.num()!=0 || req!=0 || pending_cnt!=0) $fatal(1,"SV_TC_DRAIN_FAILED");
        repeat (8) sv_step(2);
        $fclose(sv_samples); $fclose(sv_solutions);
        $display("SV_TC_DONE case=%s slots=%0d edges=%0d active_edges=%0d resets=%0d cancelled=%0d",
            sv_name,sv_frames,cycle,sv_active,wave_resets,wave_aborted);
    end
 endtask
