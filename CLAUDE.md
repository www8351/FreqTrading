You operate under a strict, file-based lifecycle management protocol. Attached are the specifications for 5 core management files: README.md, STATUS.md, PROGRESS.md, DECISIONS.md, and CLAUDE_MEMORY.md. You must enforce them as your absolute roadmap and source of truth.

## Execution Rules:

1. **Bootstrap on Demand:** Upon initialization, scan the workspace root. If any of these 5 files are missing, you must immediately create them from scratch, applying the exact structural rules defined in the attached specifications.
2. **Context Alignment:** Before writing any code, modifying files, or answering queries, read these files to align with the current architecture, memory constraints, and active state.
3. **Automated Synchronization:** You are strictly required to update these files autonomously at the end of every task or interaction:
   * **Code changes / Task completion:** Update STATUS.md and append a dated entry to PROGRESS.md.
   * **Architectural shifts / Rejected paths:** Document inside DECISIONS.md.
   * **Persona / Rule updates:** Modify CLAUDE_MEMORY.md.
   * **Global stack changes:** Modify README.md.

Do not wait for explicit permission or a separate command to update the state. Maintain this loop seamlessly in the background across all terminal operations.

# PROGRESS.md
* Uses dated entries and tracks what happened over time
* Records what you worked on, what changed, what you tried
* Records what worked and what did not
* Helps you understand the timeline of the project

---

# STATUS.md
* Shows where the project stands right now
* Tracks what has already been done / what is still open
* Lists the next best action and blockers or things waiting on someone
* Captures anything that needs review
* Helps you restart without scrolling through old chats
* Should be updated at the end of each work session

---

# README.md
* Explains what the project is / who the project is for / why the project matters
* Stores the background context that does not change every session
* Includes useful links, reference files, examples, constraints, and tone notes
* Helps a new AI understand the project before doing any work
* Good for anything you find yourself re-explaining over and over
* Should only change when the bigger project direction changes

---

# DECISIONS.md
* The decision log
* Tracks decisions you have already made and explains why those decisions were made
* Notes options you rejected
* Notes whether a decision is final or can be revisited later
* Stops the AI from reopening questions you already settled
* Keeps the project from going in circles across different AI chats

# CLAUDE_MEMORY.md
* Stores long-term memory, core preferences, and persistent behavioral rules for the AI
* Defines the strict technical stack, architectural constraints, and coding style requirements
* Explicitly states communication preferences (e.g., direct, concise, no conversational pleasantries)
* Holds sensitive environment configurations, security guidelines, or hardening practices to enforce
* Prevents the AI from resetting its persona or forgetting operational constraints across new sessions
* Should be updated only when core developer preferences, global rules, or tech stacks evolve
