// The solver supplies pin values sampled on subsequent rising edges.
class cmdbuf_formal_cover_tc extends cmdbuf_main_tc;
    logic [FORMAL_PIN_BITS-1:0] frames[];

    function new(input int count);
        num_cycles=count;
        frames=new[count];
    endfunction

    task load_frames(input int stream);
        for (int index=0; index<num_cycles; index++) begin
            if ($fscanf(stream,"%h",frames[index])!=1 || $isunknown(frames[index]))
                env_fail("FORMAL_BAD_FRAME",$sformatf("frame %0d",index),1);
        end
    endtask

    virtual task main_phase;
        logic [128:0] memory;
        for (int index=0; index<num_cycles; index++) begin
            @(posedge clk);
            begin_cycle(index);
            `include "functional_stimulus.svh"
            drive_control();
            sample_context();
            // At issue time the memory payload is arbitrary. Its association
            // with this ISSUE is fixed: the pins carry it four edges later.
            memory='0;
            if (index+4<num_cycles) memory=frames[index+4][128:0];
            {issue_data,issue_error}=memory;
            drive_inputs();
        end
    endtask
endclass
