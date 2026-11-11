// Reactive driver owns only the port groups explicitly selected by the case.
reg protocol_mode = 0;
integer p_ar_auto, p_bank_auto, p_mem_auto, p_nreq, p_limit, p_wait;
integer p_queue[$];
integer p_src[0:1023], p_addr[0:1023], p_id[0:1023], p_port[0:1023], p_resp[0:1023];
reg [127:0] p_data[0:1023];
reg p_error[0:1023];
integer p_deadline[0:1023], p_release_edge[0:1023], p_order[0:1023];
reg p_live[0:1023], p_issued[0:1023];
integer p_due_token[integer];
integer p_serial = 0, p_submitted = 0, p_qcancel = 0, p_send = 0, p_mem_error = 0;
integer p_actions, p_frame_column = 0;

task automatic protocol_fail(input string rule);
    integer diagnostic;
    begin
        diagnostic = $fopen("constraint_failure.json", "w");
        $fdisplay(diagnostic,"{\"rule\":\"%s\",\"cycle\":%0d,\"column\":%0d,\"time_ps\":%0.0f,\"arready\":\"%b\",\"arvalid\":\"%b\",\"src\":\"%b\",\"arresp\":\"%b\",\"req\":\"%b\",\"arb_addr\":\"%h\",\"ar_rd_gnt\":\"%b\",\"rw_rd_gnt\":\"%b\",\"status\":[\"%b\",\"%b\",\"%b\",\"%b\",\"%b\",\"%b\",\"%b\",\"%b\"]}",
            rule,cycle,p_frame_column,$realtime*1000.0,arready,arvalid,src,arresp,req,arb_addr,ar_rd_gnt,rw_rd_gnt,
            rd_mem_status[0],rd_mem_status[1],rd_mem_status[2],rd_mem_status[3],rd_mem_status[4],rd_mem_status[5],rd_mem_status[6],rd_mem_status[7]);
        $fclose(diagnostic);
        $fatal(1,"ENV_CONFLICT rule=%s cycle=%0d column=%0d ready=%b valid=%b req=%b",rule,cycle,p_frame_column,arready,arvalid,req);
    end
endtask

task automatic protocol_reset;
    begin
        p_qcancel = p_qcancel + p_queue.size();
        p_queue.delete(); p_due_token.delete();
        for (integer i=0;i<1024;i=i+1) begin p_live[i]=0; p_issued[i]=0; end
        if (p_ar_auto) arvalid=0;
        if (p_mem_auto) begin mem_rdata=0; pc_err=0; end
    end
endtask

task automatic protocol_prepare;
    integer token, lookup;
    begin
        if (!rst_n) begin
            if (p_ar_auto) arvalid=0;
            if (p_mem_auto) begin mem_rdata=0; pc_err=0; end
        end else begin
            if ($isunknown({arready,req}) || ((|req) && $isunknown(arb_addr))) protocol_fail("P_KNOWN_FEEDBACK");
            if (p_ar_auto) begin
                arvalid=0;
                for (integer j=0;j<p_queue.size();j=j+1) begin
                    token=p_queue[j];
                    if (cycle-p_release_edge[token]>p_wait ||
                       (p_deadline[token]>=0 && cycle>p_deadline[token])) protocol_fail("P_QUEUE_TIMEOUT");
                end
                if (p_queue.size()>0 && p_send) begin
                    token=p_queue[0];
                    src=p_src[token]; araddr=p_addr[token]; arid=p_id[token];
                    ar_port_id=p_port[token]; arresp=p_resp[token];
                    arvalid=|arready;
                    if (inject=="src" && arvalid) src=3;
                    if (inject=="arresp" && arvalid) arresp=1;
                end
            end
            if (p_bank_auto) begin
                for (integer b=0;b<8;b=b+1)
                    if (req[b] && ar_rd_gnt[b] && rw_rd_gnt[b])
                        rd_mem_status[b][arb_addr[0]]=(inject=="busy") ? 0 : 1;
            end
            if (p_mem_auto) begin
                mem_rdata=0; pc_err=0;
                lookup=cycle;
                if (inject=="mem_early") lookup=cycle+1;
                if (inject=="mem_late") lookup=cycle-1;
                if (p_due_token.exists(lookup)) begin
                    token=p_due_token[lookup]; mem_rdata=p_data[token]; pc_err=p_error[token];
                    if (inject=="mem_corrupt") mem_rdata=mem_rdata ^ 128'd1;
                end
            end
            // Checks cover both fixed and automatic ownership. No fixed value
            // is repaired, and no DUT output is driven by this adapter.
            if ($isunknown({arvalid,src,araddr,arid,ar_port_id,arresp,rready,
                            ar_rd_gnt,rw_rd_gnt,mem_rdata,pc_err})) protocol_fail("P_KNOWN");
            if (arvalid && src>=3) protocol_fail("E03_SRC");
            if (arvalid && !(arresp==0 || arresp==3)) protocol_fail("E04_ARRESP");
            if (arready==0 && arvalid) protocol_fail("E05_ZERO_READY");
            for (integer b=0;b<8;b=b+1) begin
                if ($isunknown(rd_mem_status[b])) protocol_fail("P_KNOWN_STATUS");
                if (req[b] && ar_rd_gnt[b] && rw_rd_gnt[b] && rd_mem_status[b][arb_addr[0]]!==1'b1)
                    protocol_fail("E06_SUCCESS_BUSY");
            end
            if (p_due_token.exists(cycle)) begin
                token=p_due_token[cycle];
                if (mem_rdata!==p_data[token] || pc_err!==p_error[token]) protocol_fail("T_MEM4_VALUE");
            end
            $fdisplay(p_actions,"%0d,%0d,PREPARE,%0d,%b,%b,%b,%0d",cycle,p_frame_column,
                      p_queue.size()>0 ? p_queue[0] : -1,p_send,arready,arvalid,p_queue.size());
        end
    end
endtask

task automatic protocol_observe;
    integer token, best, bank;
    reg success;
    begin
        if (rst_n) begin
            if (p_ar_auto && p_send && p_queue.size()>0 && arready==0) ar_gated=ar_gated+1;
            if (p_due_token.exists(cycle)) p_due_token.delete(cycle);
            success=|(req & ar_rd_gnt & rw_rd_gnt);
            if (success) begin
                best=-1; bank=-1;
                for (integer b=0;b<8;b=b+1) if (req[b]) bank=b;
                for (integer i=0;i<1024;i=i+1)
                    if (p_live[i] && !p_issued[i] && (p_addr[i]>>6)==arb_addr && ((p_addr[i]>>3)&7)==bank)
                        if (best<0 || p_order[i]<p_order[best]) best=i;
                if (best<0) protocol_fail("T_ASSOCIATION_ISSUE");
                if (p_due_token.exists(cycle+4)) protocol_fail("T_MEM4_COLLISION");
                p_issued[best]=1; p_due_token[cycle+4]=best;
                due_data[cycle+4]=p_data[best]; due_parity[cycle+4]=p_error[best];
                $fdisplay(p_actions,"%0d,%0d,ISSUE_TOKEN,%0d,0,%b,%b,%0d",cycle,p_frame_column,best,arready,arvalid,p_queue.size());
            end
            if (arvalid && src<3 && arready[src]) begin
                if (p_ar_auto) begin
                    if (p_queue.size()==0) protocol_fail("P_QUEUE_EMPTY");
                    token=p_queue.pop_front();
                end else begin
                    token=p_serial;
                    if (token>=1024) protocol_fail("P_QUEUE_TOKEN_LIMIT");
                    p_src[token]=src; p_addr[token]=araddr; p_id[token]=arid; p_port[token]=ar_port_id; p_resp[token]=arresp;
                    p_data[token]=memory_pattern(token,araddr[17:6]); p_error[token]=p_mem_error;
                    p_submitted=p_submitted+1;
                end
                p_live[token]=1; p_issued[token]=0; p_order[token]=p_serial; p_serial=p_serial+1;
                $fdisplay(p_actions,"%0d,%0d,ACCEPT_TOKEN,%0d,0,%b,%b,%0d",cycle,p_frame_column,token,arready,arvalid,p_queue.size());
            end
        end
    end
endtask

task automatic run_protocol;
    string magic;
    integer rows,require_empty,nread,prepare,release_token,j,token;
    longint frame_ps,previous_ps;
    reg w_clk,w_rst,w_valid,w_error;
    reg [1:0] w_src,w_resp,w_status[0:7];
    reg [17:0] w_addr;
    reg [7:0] w_id,w_ag,w_rg;
    reg [4:0] w_port;
    reg [2:0] w_ready;
    reg [127:0] w_data;
    begin
        nread=$fscanf(fd,"%s %d %d %d %d %d %d %d %d %d %d %d\n",magic,rows,wave_tick_ps,wave_duration_ps,
                     wave_skew_ps,require_empty,p_ar_auto,p_bank_auto,p_mem_auto,p_nreq,p_limit,p_wait);
        if (nread!=12 || magic!="PROTOCOL_V1" || p_nreq>1024) $fatal(1,"Malformed protocol header");
        for (j=0;j<1024;j=j+1) begin p_live[j]=0; p_issued[j]=0; end
        for (j=0;j<p_nreq;j=j+1) begin
            nread=$fscanf(fd,"%d %d %h %h %h %h %h %d %d\n",token,p_src[j],p_addr[j],p_id[j],p_port[j],p_resp[j],p_data[j],p_error[j],p_deadline[j]);
            if (nread!=9 || token!=j) $fatal(1,"Malformed protocol request");
        end
        wave_samples=$fopen("samples.csv","w"); wave_driven=$fopen("driven.csv","w");
        p_actions=$fopen("protocol_events.csv","w");
        $fdisplay(p_actions,"cycle,column,event,token,send_enable,arready,arvalid,queue_depth");
        $fdisplay(wave_samples,"cycle,column,time_ps,clk,rst_n,arvalid,src,araddr,arid,ar_port_id,arresp,rready,ar_rd_gnt,rw_rd_gnt,rd_mem_status[0],rd_mem_status[1],rd_mem_status[2],rd_mem_status[3],rd_mem_status[4],rd_mem_status[5],rd_mem_status[6],rd_mem_status[7],mem_rdata,pc_err,arready,req,arb_addr,pending_cnt,rvalid,rdata,rid,r_port_id,raddr,rresp");
        $fdisplay(wave_driven,"time_ps,clk,rst_n,arvalid,src,araddr,arid,ar_port_id,arresp,rready,ar_rd_gnt,rw_rd_gnt,rd_mem_status[0],rd_mem_status[1],rd_mem_status[2],rd_mem_status[3],rd_mem_status[4],rd_mem_status[5],rd_mem_status[6],rd_mem_status[7],mem_rdata,pc_err");
        previous_ps=0; wave_started=1;
        for (j=0;j<rows;j=j+1) begin
            nread=$fscanf(fd,"%d %d %d %d %d %b %b %b %b %b %b %b %b %b %b %b %b %b %b %b %b %b %b %b %b %b\n",
                frame_ps,prepare,release_token,p_send,p_mem_error,w_clk,w_rst,w_valid,w_src,w_addr,w_id,w_port,w_resp,w_ready,w_ag,w_rg,
                w_status[0],w_status[1],w_status[2],w_status[3],w_status[4],w_status[5],w_status[6],w_status[7],w_data,w_error);
            if (nread!=26 || frame_ps<previous_ps) $fatal(1,"Malformed protocol frame");
            #((frame_ps-previous_ps)/1000.0);
            p_frame_column=frame_ps/wave_tick_ps;
            rst_n=w_rst; rready=w_ready; ar_rd_gnt=w_ag; rw_rd_gnt=w_rg;
            if (!p_ar_auto) begin arvalid=w_valid; src=w_src; araddr=w_addr; arid=w_id; ar_port_id=w_port; arresp=w_resp; end
            if (!p_mem_auto) begin mem_rdata=w_data; pc_err=w_error; end
            // Automatic baseline is updated before each prepare, never restored
            // over the resolved busy value at the subsequent sampling edge.
            if (!p_bank_auto || prepare || !rst_n)
                for (integer b=0;b<8;b=b+1) rd_mem_status[b]=w_status[b];
            #0; // allow async reset and combinational ready/req to settle
            if (release_token>=0) begin
                if (!rst_n) protocol_fail("T_CLOCK_RESET_RELEASE");
                if (p_queue.size()>=p_limit) protocol_fail("P_QUEUE_LIMIT");
                p_queue.push_back(release_token); p_release_edge[release_token]=cycle; p_submitted=p_submitted+1;
                $fdisplay(p_actions,"%0d,%0d,RELEASE,%0d,0,%b,%b,%0d",cycle,p_frame_column,release_token,arready,arvalid,p_queue.size());
            end
            if (prepare) protocol_prepare();
            #0; clk=w_clk;
            $fdisplay(wave_driven,"%0d,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b,%b",frame_ps,
                clk,rst_n,arvalid,src,araddr,arid,ar_port_id,arresp,rready,ar_rd_gnt,rw_rd_gnt,
                rd_mem_status[0],rd_mem_status[1],rd_mem_status[2],rd_mem_status[3],rd_mem_status[4],rd_mem_status[5],rd_mem_status[6],rd_mem_status[7],mem_rdata,pc_err);
            previous_ps=frame_ps;
        end
        $fclose(fd); #0.001;
        if (require_empty && (p_queue.size()!=0 || p_due_token.num()!=0 || accepted!=retired+wave_aborted ||
            accepted!=issued+wave_aborted_unissued || issued!=captured+wave_aborted_mem ||
            captured!=retired+wave_aborted_response || req!=0 || (monitor.strict_mode && pending_cnt!=0))) drain_failure("P_QUEUE_PENDING");
        $fclose(wave_samples); $fclose(wave_driven); $fclose(p_actions);
    end
endtask
