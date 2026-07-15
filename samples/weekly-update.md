# Data Platform Team - Raw Notes, Week of Jul 8-14 2026

These are messy raw notes. The job reads this file from the input volume and asks
the inside-Databricks LLM to turn it into a clean deliverable in the output volume.
(Later, the branded-pptx skill will turn a document like this into a slide deck.)

- migrated the ingestion pipeline to serverless, cut cluster cost roughly in half
- three agents prototyped: ingestion, schema discovery, query optimizer. smoke tests passing
- unity catalog secrets went GA this week, moved all tokens off notebooks into UC secrets
- query optimizer knocked a 12 min spark job down to about 8 min on the 500M row table
- security review done, added role based access + token scoped perms on the notebook endpoints
- still blocked on: getting Claude opus endpoint enabled (needs paid tier, free tier rate-limited to 0)
- next week: demo deck for product + compliance, decide on paid tier, start branded-pptx re-cut
