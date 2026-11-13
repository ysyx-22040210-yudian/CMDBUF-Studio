// 过程式合法随机：在每个 if 分支内按当前合法取值范围生成随机数。
// 用户只需修改 main_phase 的赋值和 if 条件；没有 constraint/randomize。
// 每次 @(posedge clk) 后执行一组带条件赋值，供下一上升沿采样。
class tc_random extends cmdbuf_main_tc;
    function new(); num_cycles=2000; endfunction

    virtual task main_phase;
        for (int unsigned cycle=0; cycle<num_cycles; cycle++) begin
            @(posedge clk);
            // begin_cycle 等待本沿 NBA 完成。
            begin_cycle(cycle);

            // 编辑区 A：复位保持 1；时钟单位 ns，pause 延长下一段低电平。
            rst_n = 1; // Environment: low at t=0, released at #1 (1 ns).
            rready = $urandom_range(7,0);
            clk_low_ns = 5; clk_high_ns = 5; clk_pause_ns = 0;
            drive_control();
            // sample_context 等待同一时间点的反馈稳定。
            sample_context();

            // 编辑区 B：选中的 src 必须 ready，空闲拍使用 src=3。
            begin
                logic [1:0] ready_sources[3];
                int ready_count;
                ready_count = 0;
                for (int lane=0; lane<3; lane++)
                    if (arready[lane] === 1'b1) begin
                        ready_sources[ready_count] = lane[1:0];
                        ready_count++;
                    end
                if (!rst_n || ready_count == 0) arvalid = 0;
                else arvalid = $urandom_range(1,0);
                if (arvalid) src = ready_sources[$urandom_range(ready_count-1,0)];
                else src = 3;
            end
            if (arvalid) begin
                if ($urandom_range(1,0)) arresp = 3;
                else arresp = 0;
            end else arresp = $urandom_range(3,0);
            araddr = $urandom_range(18'h3ffff,0);
            arid = $urandom_range(255,0); ar_port_id = $urandom_range(31,0);
            rw_rd_gnt = $urandom_range(255,0);

            // E06：8 个 Bank 的所选半区，成功发射时必须 busy。
            for (int b=0; b<8; b++) begin
                if (req[b]) ar_rd_gnt[b] = $urandom_range(1,0);
                else ar_rd_gnt[b] = 0;
                for (int h=0; h<2; h++) begin
                    if (req[b] && ar_rd_gnt[b] && rw_rd_gnt[b] && h==arb_addr[0])
                        rd_mem_status[b][h] = 1;
                    else
                        rd_mem_status[b][h] = $urandom_range(1,0);
                end
            end

            // 本拍若 ISSUE 成功，预约 N+4 的返回数据。用户在此改数据/错误位。
            issue_data = {$urandom(),$urandom(),$urandom(),$urandom()};
            issue_error = $urandom_range(1,0);
            // MEM 有返回任务时按历史驱动；无任务时可固定值或随机。
            if (mem_due) begin
                mem_rdata = due_data;
                pc_err = due_error;
            end else begin
                mem_rdata = {$urandom(),$urandom(),$urandom(),$urandom()};
                pc_err = $urandom_range(1,0);
            end
            drive_inputs(); // 只校验并原样驱动，不在框架里改写 TC 的值。
        end
    endtask
endclass
