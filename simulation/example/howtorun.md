YAML based workflow:

# generate dependency graph
See README.md at root
Set
make -f Makefile eval FILE=./tests/mctest4.yaml MONITOR=dependency

JSONL based workflow (deprecated):

# generate dependency grpah
make -f Makefile graph transcripts/[transcript].jsonl

# run multicontext
post-index [n] should be we are running on context n
