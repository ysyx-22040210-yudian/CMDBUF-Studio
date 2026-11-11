# Verdi O-2018.09: add protocol signals to the FSDB opened by -ssf.
set wave [wvGetCurrentWindow]
wvAddSignal -win $wave -clear -group {CLOCK {/tb_cmdbuf_rd/clk} {/tb_cmdbuf_rd/rst_n} {/tb_cmdbuf_rd/cycle}}
wvAddSignal -win $wave -group {AR {/tb_cmdbuf_rd/arvalid} {/tb_cmdbuf_rd/arready} {/tb_cmdbuf_rd/src} {/tb_cmdbuf_rd/araddr} {/tb_cmdbuf_rd/arid} {/tb_cmdbuf_rd/ar_port_id} {/tb_cmdbuf_rd/arresp}}
wvAddSignal -win $wave -group {ISSUE {/tb_cmdbuf_rd/req} {/tb_cmdbuf_rd/arb_addr} {/tb_cmdbuf_rd/ar_rd_gnt} {/tb_cmdbuf_rd/rw_rd_gnt} {/tb_cmdbuf_rd/pending_cnt}}
wvAddSignal -win $wave -group {BANK {/tb_cmdbuf_rd/rd_mem_status[0]} {/tb_cmdbuf_rd/rd_mem_status[1]} {/tb_cmdbuf_rd/rd_mem_status[2]} {/tb_cmdbuf_rd/rd_mem_status[3]} {/tb_cmdbuf_rd/rd_mem_status[4]} {/tb_cmdbuf_rd/rd_mem_status[5]} {/tb_cmdbuf_rd/rd_mem_status[6]} {/tb_cmdbuf_rd/rd_mem_status[7]}}
wvAddSignal -win $wave -group {MEM {/tb_cmdbuf_rd/mem_rdata} {/tb_cmdbuf_rd/pc_err}}
wvAddSignal -win $wave -group {R {/tb_cmdbuf_rd/rvalid} {/tb_cmdbuf_rd/rready} {/tb_cmdbuf_rd/rdata} {/tb_cmdbuf_rd/rid} {/tb_cmdbuf_rd/r_port_id} {/tb_cmdbuf_rd/raddr} {/tb_cmdbuf_rd/rresp}}
wvZoomAll -win $wave
puts "EDA_WAVE_PRESET_READY"
