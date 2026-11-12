#include <cstddef>
#include <fstream>
#include <iostream>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>

#include "npi.h"
#include "npi_cov.h"

namespace {

const std::map<npiCovObjType_e, const char *> object_names = {
    {npiCovBlock, "block"}, {npiCovStmtBin, "statement"},
    {npiCovSignal, "signal"}, {npiCovSignalBit, "bit"}, {npiCovToggleBin, "direction"},
    {npiCovFsm, "machine"}, {npiCovStates, "states"}, {npiCovTransitions, "transitions"},
    {npiCovSequences, "sequences"}, {npiCovStateBin, "state"},
    {npiCovTransBin, "transition"}, {npiCovSeqBin, "sequence"},
    {npiCovCondition, "expression"}, {npiCovConditionBin, "vector"}, {npiCovConditionTerm, "term"},
    {npiCovBranch, "decision"}, {npiCovBranchBin, "outcome"}, {npiCovBranchTerm, "term"},
    {npiCovAssert, "assertion"}, {npiCovCoverProperty, "cover property"},
    {npiCovCoverSequence, "cover sequence"}, {npiCovSuccessBin, "success"},
    {npiCovAttemptBin, "attempt"}, {npiCovFailureBin, "failure"}, {npiCovVacuousBin, "vacuous"},
    {npiCovIncompleteBin, "incomplete"}, {npiCovFirstmatchBin, "first match"},
    {npiCovCovergroup, "covergroup"}, {npiCovCoverInstance, "group instance"},
    {npiCovCoverpoint, "coverpoint"}, {npiCovCross, "cross"}, {npiCovCoverBin, "bin"},
};

const std::pair<npiCovObjType_e, const char *> metrics[] = {
    {npiCovLineMetric, "LINE"}, {npiCovConditionMetric, "COND"}, {npiCovToggleMetric, "TOGGLE"},
    {npiCovBranchMetric, "BRANCH"}, {npiCovFsmMetric, "FSM"},
    {npiCovAssertMetric, "ASSERT"}, {npiCovTestbenchMetric, "GROUP"},
};

bool code_bin(npiCovObjType_e type) {
    return type == npiCovStmtBin || type == npiCovToggleBin || type == npiCovConditionBin ||
           type == npiCovBranchBin || type == npiCovStateBin || type == npiCovTransBin ||
           type == npiCovSeqBin;
}

std::string property(npiCovProperty_e key, npiCovHandle object) {
    const char *value = npi_cov_get_str(key, object);
    return value ? value : "";
}

std::string quote(const std::string &value) {
    const char hex[] = "0123456789abcdef";
    std::string result = "\"";
    for (unsigned char ch : value) {
        if (ch == '"' || ch == '\\') result += '\\';
        if (ch < 32) {
            result += "\\u00";
            result += hex[ch >> 4];
            result += hex[ch & 15];
        } else {
            result += static_cast<char>(ch);
        }
    }
    return result + '"';
}

// One hexadecimal digit per four stable bin indices, least significant bin first
// within each digit. Emit the high digits first for Python's arbitrary-size int.
std::string bitmap(const std::vector<bool> &bits) {
    const char hex[] = "0123456789abcdef";
    std::string result((bits.size() + 3) / 4, '0');
    for (std::size_t i = 0; i < result.size(); ++i) {
        unsigned digit = 0;
        for (unsigned b = 0; b < 4 && 4*i+b < bits.size(); ++b)
            if (bits[4*i+b]) digit |= 1u << b;
        result[result.size()-1-i] = hex[digit];
    }
    return result.empty() ? "0" : result;
}

class Iterator {
public:
    explicit Iterator(npiCovHandle handle) : handle_(handle) {}
    ~Iterator() { if (handle_) npi_cov_iter_stop(handle_); }
    Iterator(const Iterator &) = delete;
    Iterator &operator=(const Iterator &) = delete;
    npiCovHandle next() const { return handle_ ? npi_cov_iter_next(handle_) : nullptr; }
private:
    npiCovHandle handle_;
};

struct Location {
    std::string file;
    int line;
};

Location location(npiCovHandle object, const Location &parent) {
    std::string file = property(npiCovFileName, object);
    const int line = npi_cov_get(npiCovLineNo, object, nullptr);
    return {file.empty() ? parent.file : file, line > 0 ? line : parent.line};
}

class DatabaseReader {
public:
    DatabaseReader(std::ostream &details, std::ostream &tests) : details_(details), tests_(tests) {}

    void read(const std::string &key, const std::string &expected, const std::string &path) {
        auto database = npi_cov_open(path.c_str());
        if (!database) throw std::runtime_error("Cannot open VDB: " + path);
        try {
            Iterator tests(npi_cov_iter_start(npiCovTest, database));
            test_ = tests.next();
            if (!test_) throw std::runtime_error("VDB has no test: " + path);
            const std::string fullname = property(npiCovName, test_);
            const std::string name = fullname.substr(fullname.find_last_of('/') + 1);
            if ((!expected.empty() && name != expected) || tests.next())
                throw std::runtime_error("Expected one test " + expected + " in " + path + ", got " + fullname);
            covered_.clear();
            eligible_.clear();
            read_instances(database, "");
            if (shape_.size() != covered_.size() || shape_.empty())
                throw std::runtime_error("Code coverage structure is missing or changed: " + path);
            tests_ << "{\"key\":" << quote(key) << ",\"test\":" << quote(name)
                   << ",\"covered\":" << quote(bitmap(covered_))
                   << ",\"eligible\":" << quote(bitmap(eligible_)) << "}\n";
            tests_.flush();
            capture_ = false;
        } catch (...) {
            npi_cov_close(database);
            throw;
        }
        npi_cov_close(database);
    }

private:
    void read_instances(npiCovHandle parent, const std::string &parent_name) {
        Iterator children(npi_cov_iter_start(npiCovInstance, parent));
        while (auto instance = children.next()) {
            const std::string name = property(npiCovFullName, instance);
            const std::string module = property(npiCovDefName, instance);
            const Location loc = location(instance, {"", -1});
            if (capture_)
                details_ << "{\"instance\":" << quote(name) << ",\"parent_instance\":" << quote(parent_name)
                         << ",\"module\":" << quote(module) << ",\"file\":" << quote(loc.file)
                         << ",\"line\":" << loc.line << "}\n";
            for (const auto &metric : metrics) {
                auto handle = npi_cov_handle(metric.first, instance);
                if (!handle) continue;
                const bool functional = metric.first == npiCovAssertMetric || metric.first == npiCovTestbenchMetric;
                if (functional && !capture_) continue;
                read_children(handle, name, metric.second, name + '\0' + module + '\0' + metric.second, loc, -1);
            }
            read_instances(instance, name);
        }
    }

    void read_children(npiCovHandle parent, const std::string &instance, const char *metric,
                       const std::string &parent_key, const Location &parent_loc, int parent_id) {
        Iterator children(npi_cov_iter_start(npiCovChild, parent));
        int ordinal = 0;
        while (auto child = children.next()) {
            const auto type = static_cast<npiCovObjType_e>(npi_cov_get(npiCovType, child, nullptr));
            const std::string name = property(npiCovName, child);
            const Location loc = location(child, parent_loc);
            // Include the full ancestry, ordinal and source location. Equal counts
            // or matching text at different generate instances are insufficient.
            const std::string key = parent_key + '\0' + std::to_string(ordinal++) + '\0' +
                std::to_string(type) + '\0' + name + '\0' + loc.file + '\0' + std::to_string(loc.line);
            const bool is_bin = code_bin(type);
            const int covered = capture_ || is_bin ? npi_cov_get(npiCovCovered, child, test_) : -1;
            const int total = capture_ || is_bin ? npi_cov_get(npiCovCoverable, child, test_) : -1;
            int bin = -1;
            if (is_bin) {
                bin = static_cast<int>(covered_.size());
                if (covered < 0 || total < 0 || covered > total || total > 1)
                    throw std::runtime_error("Unsupported code bin in " + instance + ": " + name);
                if (capture_) shape_.push_back(key);
                else if (covered_.size() >= shape_.size() || shape_[covered_.size()] != key)
                    throw std::runtime_error("Different code coverage structure at " + instance + " " + metric + ": " + name);
                covered_.push_back(covered > 0);
                eligible_.push_back(total > 0);
            }
            int item_id = -1;
            if (capture_) {
                item_id = next_id_++;
                const auto kind = object_names.find(type);
                details_ << "{\"id\":" << item_id << ",\"parent\":" << parent_id << ",\"bin\":" << bin
                         << ",\"instance\":" << quote(instance) << ",\"metric\":" << quote(metric)
                         << ",\"kind\":" << quote(kind == object_names.end() ? "object" : kind->second)
                         << ",\"name\":" << quote(name) << ",\"file\":" << quote(loc.file) << ",\"line\":" << loc.line
                         << ",\"covered\":" << covered << ",\"total\":" << total
                         << ",\"status\":" << npi_cov_get(npiCovStatus, child, test_)
                         << ",\"count\":" << npi_cov_get(npiCovCount, child, test_)
                         << ",\"goal\":" << npi_cov_get(npiCovCountGoal, child, test_) << "}\n";
            }
            if (!is_bin) read_children(child, instance, metric, key, loc, item_id);
        }
    }

    std::ostream &details_, &tests_;
    npiCovHandle test_ = nullptr;
    bool capture_ = true;
    int next_id_ = 0;
    std::vector<std::string> shape_;
    std::vector<bool> covered_, eligible_;
};

}  // namespace

int main(int argc, char **argv) {
    if (argc != 4) {
        std::cerr << "Usage: coverage_reader request.tsv items.jsonl tests.jsonl\n";
        return 2;
    }
    std::ifstream request(argv[1]);
    std::ofstream items(argv[2]), tests(argv[3]);
    if (!request || !items || !tests) {
        std::cerr << "Cannot open coverage reader input or output\n";
        return 2;
    }
    int npi_argc = 1;
    char *args[] = {argv[0], nullptr};
    char **npi_argv = args;
    if (!npi_init(npi_argc, npi_argv)) {
        std::cerr << "NPI initialization failed\n";
        return 2;
    }
    try {
        DatabaseReader reader(items, tests);
        std::string line;
        bool found = false;
        while (std::getline(request, line)) {
            const auto first = line.find('\t');
            const auto second = first == std::string::npos ? first : line.find('\t', first + 1);
            if (second == std::string::npos) throw std::runtime_error("Invalid VDB request");
            reader.read(line.substr(0, first), line.substr(first+1, second-first-1), line.substr(second+1));
            found = true;
        }
        items.flush();
        if (!found || !items || !tests) throw std::runtime_error("Incomplete coverage extraction");
    } catch (const std::exception &error) {
        std::cerr << error.what() << '\n';
        npi_end();
        return 2;
    }
    npi_end();
    return 0;
}
