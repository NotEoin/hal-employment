"""The person's profile: jobs, education, skills and the rest, each fact with the documents it came from.

This package holds a stand-in `store.add_found` only, so the bulk ingest (`jobhunt.ingest`) has somewhere to
hand the facts it finds. The profile store proper (its tables, merge rules and routes) replaces it.
"""
