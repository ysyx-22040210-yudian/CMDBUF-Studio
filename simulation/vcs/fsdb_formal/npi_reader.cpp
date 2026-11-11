// Direct FSDB reader. No elaborated design, VCD or testbench logs are used.
// VC_APPS_NPI: executable linkage pp. 12-16; FSDB traversal pp. 155-158.
#include "npi.h"
#include "npi_fsdb.h"
#include <algorithm>
#include <cctype>
#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

static std::string quoted(const std::string &s) {
    std::ostringstream o; o << '"';
    for (unsigned char c : s) {
        if (c == '"' || c == '\\') o << '\\' << c;
        else if (c < 32) { const char *h="0123456789abcdef"; o << "\\u00" << h[c>>4] << h[c&15]; }
        else o << c;
    }
    o << '"'; return o.str();
}
static std::string str(const char *s) { return s ? s : ""; }
static int prop(npiFsdbSigHandle s, npiFsdbSigPropertyType p) {
    int result=0;
    if (!npi_fsdb_sig_property(p,s,&result)) throw std::runtime_error("Cannot read signal property");
    return result;
}
static void signal_info(npiFsdbSigHandle s, std::ostream &out, int inherited=0) {
    int direction=prop(s,npiFsdbSigDirection);
    if (!direction) direction=inherited;
    if (prop(s,npiFsdbSigHasMember)) {
        auto it=npi_fsdb_iter_member(s); npiFsdbSigHandle member;
        while (it && (member=npi_fsdb_iter_sig_next(it))) signal_info(member,out,direction);
        return;
    }
    if (!direction) return;
    out << "{\"type\":\"signal\",\"name\":" << quoted(str(npi_fsdb_sig_property_str(npiFsdbSigFullName,s)))
        << ",\"width\":" << prop(s,npiFsdbSigRangeSize) << ",\"direction\":" << direction << "}\n";
}
static void scope_info(npiFsdbScopeHandle s,std::ostream &out,unsigned &count) {
    if (++count>20000) throw std::runtime_error("More than 20000 scopes; choose a narrower scope");
    out << "{\"type\":\"scope\",\"name\":" << quoted(str(npi_fsdb_scope_property_str(npiFsdbScopeFullName,s))) << "}\n";
    auto it=npi_fsdb_iter_child_scope(s); npiFsdbScopeHandle child;
    while (it && (child=npi_fsdb_iter_scope_next(it))) scope_info(child,out,count);
}
static std::string value(npiFsdbVctHandle v, unsigned width) {
    npiFsdbValue data; data.format=npiFsdbBinStrVal;
    if (!npi_fsdb_vct_value(v,&data)) throw std::runtime_error("Cannot read FSDB value");
    std::string s=str(data.value.str);
    std::transform(s.begin(),s.end(),s.begin(),[](unsigned char c){return std::tolower(c);});
    if (s.empty() || s.find_first_not_of("01xz")!=std::string::npos || s.size()>width)
        throw std::runtime_error("Unsupported FSDB binary value: "+s);
    if (s.size()<width) s=std::string(width-s.size(),s[0]=='x'||s[0]=='z'?s[0]:'0')+s;
    return s;
}
struct Signal {std::string name,path; unsigned width; npiFsdbVctHandle vct;};
static std::string at(const Signal &s,npiFsdbTime t) {
    if (!npi_fsdb_goto_time(s.vct,t)) throw std::runtime_error("No recorded value at requested time: "+s.path);
    return value(s.vct,s.width);
}

int main(int argc,char **argv) {
    if (argc!=6) {std::cerr << "Usage: npi_reader inspect|extract FSDB scope|mapping.tsv output.ndjson max_edges\n";return 2;}
    std::string mode=argv[1],file=argv[2],selection=argv[3],output=argv[4];
    unsigned long limit=std::stoul(argv[5]);
    int nargc=1;char *av[]={argv[0],nullptr};char **nav=av;
    if (!npi_init(nargc,nav)) {std::cerr << "NPI initialization failed\n";return 2;}
    npiFsdbFileHandle f=nullptr;
    try {
        if (!npi_fsdb_is_fsdb(file.c_str()) || !(f=npi_fsdb_open(file.c_str()))) throw std::runtime_error("Not a readable FSDB file");
        npiFsdbTime first=0,last=0;npi_fsdb_min_time(f,&first);npi_fsdb_max_time(f,&last);
        std::ofstream out(output);if (!out) throw std::runtime_error("Cannot create output file");
        out << "{\"type\":\"meta\",\"reader\":\"Synopsys NPI FSDB C API\",\"min_time\":" << first << ",\"max_time\":" << last
            << ",\"scale\":" << quoted(str(npi_fsdb_file_property_str(npiFsdbFileScaleUnit,f)))
            << ",\"dump_off\":" << quoted(str(npi_fsdb_file_property_str(npiFsdbFileDumpOffRange,f))) << "}\n";
        if (mode=="inspect") {
            unsigned count=0;auto it=npi_fsdb_iter_top_scope(f);npiFsdbScopeHandle s;
            while (it && (s=npi_fsdb_iter_scope_next(it))) scope_info(s,out,count);
            if (!selection.empty()) {
                auto scope=npi_fsdb_scope_by_name(f,selection.c_str(),nullptr);
                // Inspection must still reveal the hierarchy of an unfamiliar
                // imported FSDB when the environment's default scope is absent.
                out << "{\"type\":\"selection\",\"found\":" << (scope?"true":"false") << "}\n";
                if (scope) {
                    auto si=npi_fsdb_iter_sig(scope);npiFsdbSigHandle sig;
                    while (si && (sig=npi_fsdb_iter_sig_next(si))) signal_info(sig,out);
                }
            }
        } else if (mode=="extract") {
            std::ifstream input(selection);if (!input) throw std::runtime_error("Cannot open signal mapping");
            std::vector<Signal> signals;std::string line;
            while (std::getline(input,line)) {
                if (line.empty()) continue;
                std::istringstream row(line);Signal s;row>>s.name>>s.width>>s.path;
                if (!row || !s.width || s.width>65536) throw std::runtime_error("Invalid mapping row");
                auto sig=npi_fsdb_sig_by_name(f,s.path.c_str(),nullptr);
                if (!sig) throw std::runtime_error("Signal not found: "+s.path);
                if (prop(sig,npiFsdbSigRangeSize)!=static_cast<int>(s.width)) throw std::runtime_error("Signal width mismatch: "+s.path);
                if (!npi_fsdb_add_to_sig_list(f,sig)) throw std::runtime_error("Cannot load signal: "+s.path);
                s.vct=npi_fsdb_create_vct(sig);if (!s.vct) throw std::runtime_error("Cannot traverse signal: "+s.path);
                signals.push_back(s);
            }
            if (signals.size()<2 || signals[0].name!="clk" || signals[1].name!="rst_n") throw std::runtime_error("Mapping must begin with clk and rst_n");
            if (!npi_fsdb_load_vc_by_range(f,first,last)) throw std::runtime_error("Cannot load FSDB value changes");
            // Reset transitions identify separate runs from a reset state, including
            // asynchronous pulses that occur while the clock is stopped.
            auto reset=signals[1].vct;
            if (!npi_fsdb_goto_first(reset)) throw std::runtime_error("Reset has no recorded values");
            do {npiFsdbTime t;npi_fsdb_vct_time(reset,&t);out << "{\"type\":\"reset\",\"time\":" << t << ",\"value\":" << quoted(value(reset,1)) << "}\n";} while(npi_fsdb_goto_next(reset));
            auto clk=signals[0].vct;
            if (!npi_fsdb_goto_first(clk)) throw std::runtime_error("Clock has no recorded values");
            std::string previous="x";npiFsdbTime previous_edge=0;unsigned long edges=0;
            do {
                npiFsdbTime t;npi_fsdb_vct_time(clk,&t);std::string v=value(clk,1);
                if (v=="1" && previous!="1") {
                    if (t && previous!="0") throw std::runtime_error("Ambiguous clock edge from X/Z; cannot reconstruct SVA sampling");
                    if (t) {
                        if (previous_edge==t) throw std::runtime_error("Multiple clock edges at one FSDB timestamp");
                        previous_edge=t;
                        if (++edges>limit) throw std::runtime_error("Clock edge limit exceeded; no truncated Formal package was generated");
                        std::string before=at(signals[1],t-1),after=at(signals[1],t);
                        if (before!=after) throw std::runtime_error("Reset changes on the sampling edge; choose an unambiguous waveform");
                        out << "{\"type\":\"edge\",\"time\":" << t << ",\"values\":{\"clk\":\"0\"";
                        for (size_t i=1;i<signals.size();++i) out << ',' << quoted(signals[i].name) << ':' << quoted(at(signals[i],t-1));
                        out << "}}\n";
                    }
                }
                previous=v;
            } while (npi_fsdb_goto_next(clk));
            if (!edges) throw std::runtime_error("No rising clock edges recorded");
            for (auto &s:signals)npi_fsdb_release_vct(s.vct);
        } else throw std::runtime_error("Unknown reader mode");
        out.close();if (!out) throw std::runtime_error("Failed writing extraction output");
        npi_fsdb_close(f);npi_end();return 0;
    } catch (const std::exception &e) {
        std::cerr << "NPI_READ_ERROR: " << e.what() << '\n';
        if (f)npi_fsdb_close(f);npi_end();return 2;
    }
}
