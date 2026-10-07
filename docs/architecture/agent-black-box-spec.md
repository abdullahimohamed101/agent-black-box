> **Mechanical text extraction** of `Agent_Black_Box_Engineering_Design_Specification.docx` (v1.0, 2026-10-06) so agents can read it in-repo. Diagrams and tables lose some layout fidelity; the `.docx` remains the canonical artifact. Section numbers (`§N`) used across this repo refer to this document. Amendments are made by ADR, not by editing this file (see `docs/decisions/`).

AGENT BLACK BOX

Engineering Design & Execution Specification

A flight recorder, observability platform, evaluation system, and future control plane for AI agents.

Status

Build-ready architecture specification

Version

1.0

Date

October 6, 2026

Scope

Product definition through production operations and delivery

Authority

Source of truth unless superseded by an approved ADR

Ownership standard: This document intentionally makes concrete engineering choices, documents their tradeoffs, and defines explicit migration triggers. The goal is to finish a polished MVP without closing off a credible path to platform scale.

# Document Contract

This specification is meant to be used throughout the entire project lifecycle: product framing, implementation, code review, design review, testing, launch, operations, and future scaling.

Committed decisions are the default implementation path. Deferred technologies should not be introduced merely for architectural appearance; the document defines concrete triggers for adopting them. Architectural invariants are stronger than individual framework choices.

When implementation discovers a material contradiction or a better tradeoff, write an Architecture Decision Record (ADR) that states the new context, decision, consequences, and migration impact. Do not silently drift away from the design.

## Decision Status Legend

Committed: Implement this way unless a later ADR changes it.

Guardrail: Invariant that new features and refactors must preserve.

Deferred: Intentionally not selected yet; revisit only when the documented trigger is met.

Future: Valid expansion idea that is outside the current completion path.

## Revision History

Version

Date

Status

Summary

1.0

2026-10-06

Initial

Full product, architecture, low-level design, operations, security, and delivery specification

# Contents

Agent Black Box

1. Executive Summary

2. The Problem

3. Product Vision

4. Why This Project Is Interesting

5. Core Product Principles

6. Target Users

7. Core Concepts and Vocabulary

8. Primary User Experience

9. Core Features

10. Additional Features

11. Detailed UX Examples

11.1 Main dashboard

11.2 Run detail

11.3 LLM event drawer

11.4 Code diff drawer

11.5 Tool drawer

11.6 Failure analysis

12. System Architecture

13. MVP Architecture

14. Scale-Out Architecture

15. Event Model

16. Tracing Model

17. Python SDK Design

18. Framework Integrations

19. Backend Services

20. Database Design

21. Live Streaming

22. Analytics Engine

23. Cost Observability

24. Failure and Retry Analysis

25. Loop and Anomaly Detection

26. File, Git, and Code Diff Tracking

27. Terminal and Tool Tracking

28. Multi-Agent Observability

29. Replay

30. Run Comparison

31. Evaluation Platform

32. Security Layer

33. Policy and Approval System

34. Search and Investigation

35. Alerts

36. Projects, Workspaces, and Multi-Tenancy

37. Authentication and RBAC

38. Privacy and Data Handling

39. Retention and Storage

40. Plugin and Extension Architecture

41. Public API

42. OpenTelemetry Compatibility

43. Observability for Agent Black Box Itself

44. Reliability and Fault Tolerance

45. Scalability Strategy

46. Repository Structure

47. Testing Strategy

48. Development Phases

Phase 0 — Product skeleton

Phase 1 — Core tracing MVP

Phase 2 — Coding agent observability

Phase 3 — Analytics

Phase 4 — Multi-agent tracing

Phase 5 — Reliability intelligence

Phase 6 — Evaluations

Phase 7 — Replay

Phase 8 — Security

Phase 9 — Scale

49. MVP Definition

50. Demo Agent

51. LinkedIn Demo Strategy

52. Resume Positioning

53. Future Feature Backlog

54. What Not to Build First

55. Technical Decision Principles

56. Final Product Vision

Recommended First Build

57. Executive Engineering Decisions

58. Goals, Non-Goals, and Product Boundaries

59. Personas and Critical User Journeys

60. Functional Requirements

61. Non-Functional Requirements and SLOs

62. Architectural Invariants

63. Canonical Domain Model

64. Event Contract and Schema Governance

65. Ordering, Clocks, and Causality

66. Idempotency and Delivery Semantics

67. SDK Architecture

68. Python SDK Public API

69. TypeScript SDK Design

70. Framework Adapter Architecture

71. Ingestion API Low-Level Design

72. Background Processing Model

73. PostgreSQL Physical Design

74. ClickHouse Scale Design

75. Object Storage and Artifact Lifecycle

76. Live Streaming Architecture

77. Query and Search Architecture

78. Run Summary Materialization

79. Cost Engine Detailed Design

80. Failure Analysis Engine

81. Loop and Anomaly Detection Engine

82. Coding-Agent Telemetry

83. Terminal and Tool Safety Model

84. Multi-Agent Trace Semantics

85. Evaluation System Low-Level Design

86. Experiment and Run Comparison Model

87. Replay Architecture

88. Security Policy Engine

89. Approval Workflow

90. Threat Model

91. Authentication, Authorization, and RBAC

92. API Key Design

93. Privacy, Redaction, and Data Classification

94. Retention and Deletion

95. Frontend Information Architecture

96. Timeline UX Specification

97. Trace Waterfall UX

98. Run Comparison UX

99. Accessibility and UX Quality Bar

100. Frontend Technical Architecture

101. Platform API Architecture

102. API Versioning and Compatibility

103. Service Boundary Evolution

104. Deployment Environments

105. Reference Production Deployment

106. Docker and Container Standards

107. Kubernetes Scale Plan

108. Configuration Management

109. Feature Flags

110. Database Migration Strategy

111. CI Pipeline

112. CD and Release Strategy

113. SDK Release Strategy

114. Testing Pyramid

115. Chaos and Failure Testing

116. Observability of Agent Black Box

117. SLOs, SLIs, and Error Budgets

118. Backpressure Strategy

119. Capacity Model

120. Performance Engineering

121. Caching Strategy

122. Disaster Recovery

123. Backup and Restore Verification

124. Incident Response

125. Operational Runbooks

126. Security Operations

127. Dependency Management and Supply Chain

128. Repository and Ownership Structure

129. Coding Standards

130. Error Taxonomy

131. Logging Policy

132. Documentation Strategy

133. Developer Experience Acceptance Criteria

134. Local Development Workflow

135. Demo Data and Fixtures

136. Flagship Coding-Agent Demo

137. Product Analytics

138. Launch Quality Bar

139. Milestone Plan

140. Prioritization Framework

141. Architecture Decision Records

142. Major Alternatives Considered

143. Risk Register

144. Definition of Done for the Whole Project

145. Build Checklist: Empty Repository to MVP

146. Post-MVP Expansion Checklist

147. Suggested First 20 GitHub Issues

148. Interview and Design-Review Defense

149. Reference: SQL Schema Sketch

150. Reference: Core Event Examples

151. Reference: Policy DSL Example

152. Reference: Search Grammar

153. Reference: Run State Machine

154. Reference: Approval State Machine

155. Reference: Failure Matrix

156. Reference: Performance Budgets

157. Reference: Data Ownership Matrix

158. Reference: Service Sizing Heuristics

159. Reference: Glossary

160. Reference: External Standards and Technology Notes

161. Final Principal-Engineer Guidance

PART

PRODUCT DEFINITION

Problem, vision, users, product principles, and the complete feature surface.

# 1. Executive Summary

AI agents increasingly do more than generate text. They can:

read source code

edit files

execute shell commands

call APIs

query databases

access GitHub

browse the web

invoke MCP tools

start sub-agents

retry failed tasks

make multi-step decisions

interact with production systems

The problem is that once an agent performs a large workflow, it becomes difficult to answer basic debugging questions:

What exactly did the agent do?

Which tools did it call?

What files did it modify?

Which model calls were expensive?

Why did it retry?

Where did the failure begin?

Did a tool fail or did the model make a bad decision?

Did the agent enter a loop?

Did it access sensitive data?

Did two agent runs behave differently?

Would another model have performed better?

Can we replay the run?

Can we stop unsafe actions before they happen?

Agent Black Box solves this by creating a structured trace for each agent execution.

A run might look like:

```text
Run #A82F1
────────────────────────────────────────────

00:00  User:
       "Fix the authentication bug"

00:01  Agent started planning

00:02  READ
       src/auth/login.ts

00:04  READ
       src/auth/session.ts

00:06  TOOL
       GitHub.searchIssues()

00:09  LLM
       Claude
       3,482 input tokens
       1,102 output tokens
       $0.07

00:13  FILE MODIFIED
       src/auth/session.ts

00:17  TERMINAL
       npm test

00:24  ❌ 3 tests failed

00:26  RETRY #1

00:32  FILE MODIFIED
       src/auth/session.ts

00:39  TERMINAL
       npm test

00:47  ✅ 142/142 tests passed

────────────────────────────────────────────
Duration: 47 sec
Cost: $0.18
Tool calls: 12
Files changed: 2
Retries: 1
Status: SUCCESS
```

The platform begins as observability infrastructure and expands naturally into:

debugging

cost management

agent evaluation

reliability monitoring

security monitoring

agent policy enforcement

human approval workflows

replay

model comparison

enterprise governance

# 2. The Problem

Traditional software has a mature observability stack.

Engineers can use:

application logs

metrics

traces

request IDs

dashboards

alerts

profilers

distributed tracing

error tracking

audit logs

AI agents behave differently.

An agent run may consist of dozens or hundreds of actions:

```text
User request
    ↓
LLM planning
    ↓
Tool selection
    ↓
GitHub search
    ↓
File read
    ↓
LLM call
    ↓
File edit
    ↓
Terminal command
    ↓
Failure
    ↓
Retry
    ↓
Another LLM call
    ↓
Another file edit
    ↓
Tests
    ↓
Sub-agent
    ↓
Database query
    ↓
Final answer
```

A normal application trace does not capture the semantics of this workflow very well.

A generic log line such as:

```text
Agent failed to complete task
```

does not answer:

what action caused the failure

what context the agent saw

how many retries occurred

whether the agent repeated itself

whether the tool or model was responsible

whether the same task succeeds with another model

how much money was spent before failure

whether the agent made risky actions

Agent Black Box treats agent execution as a first-class distributed workflow.

# 3. Product Vision

The long-term platform has four stages.

## Stage 1 — Observe

Capture everything meaningful during an agent run:

model calls

tool calls

MCP calls

agent state transitions

terminal commands

file reads

file writes

Git operations

database queries

HTTP calls

retries

exceptions

sub-agent execution

cost

latency

## Stage 2 — Understand

Turn raw events into useful insights:

identify the failure origin

identify expensive spans

identify repeated actions

visualize execution

show code diffs

show causal relationships

calculate health metrics

search across runs

## Stage 3 — Evaluate

Use historical executions to compare:

models

prompts

tools

agent versions

architectures

latency

cost

reliability

task success

## Stage 4 — Control

Add active policy enforcement:

block risky commands

require human approval

restrict filesystem access

restrict database access

restrict network access

enforce token budgets

prevent dangerous tool calls

detect prompt injection

stop infinite loops

The result becomes more than a dashboard.

It becomes an operational control plane for autonomous software.

# 4. Why This Project Is Interesting

Agent Black Box works well as a portfolio project because it combines several disciplines:

### AI engineering

LLM integrations

agents

tool calling

MCP

evaluation

model comparison

### Distributed systems

event ingestion

event ordering

trace propagation

asynchronous processing

streaming

scalable storage

### Site reliability engineering

observability

tracing

retries

errors

dashboards

reliability scoring

anomaly detection

### Backend engineering

APIs

workers

databases

WebSockets

event pipelines

### Frontend engineering

timeline visualization

live updates

trace waterfalls

diff viewers

analytics dashboards

### Security

audit logs

sensitive-data detection

policy enforcement

approval workflows

It is technically serious while still being visually impressive.

# 5. Core Product Principles

These principles should influence every design decision.

## 5.1 Framework agnostic

Agent Black Box should not depend on one framework.

It should eventually support:

LangGraph

OpenAI Agents SDK

custom Python agents

custom TypeScript agents

MCP clients

CrewAI

AutoGen

coding agents

browser agents

workflow agents

The internal event model should remain independent of the source framework.

## 5.2 Event-first architecture

Everything important should become a structured event.

The frontend should never depend on a specific framework's raw logs.

Instead:

```text
Framework
    ↓
Adapter
    ↓
Canonical Agent Black Box Event
    ↓
Ingestion
    ↓
Storage + Analytics + UI
```

## 5.3 Small MVP, scalable abstractions

The first release should not require:

Kafka

Kubernetes

ClickHouse

dozens of microservices

However, the system boundaries should allow those technologies to be introduced later.

## 5.4 Never block the agent unnecessarily

The tracing SDK should add minimal overhead.

Agent execution should continue even if the telemetry backend becomes temporarily unavailable.

The SDK should support:

local buffering

batching

asynchronous export

retries

sampling

## 5.5 Structured data over raw text

Raw logs can be stored, but important information should be normalized.

Bad:

```text
"Calling tool github"
```

Better:

```text
{
  "event_type": "tool_call",
  "tool": "github",
  "operation": "search_issues",
  "latency_ms": 843,
  "status": "success"
}
```

## 5.6 Extensibility first

A new event type, analyzer, model provider, storage backend, or integration should not require changing the entire system.

# 6. Target Users

## 6.1 Individual agent developers

They want to understand why their agent failed.

Primary needs:

trace timeline

model calls

tool calls

errors

token use

cost

debugging

## 6.2 AI infrastructure teams

They run many agents in production.

Primary needs:

dashboards

reliability

latency

costs

error rates

comparisons

alerts

## 6.3 Engineering teams adopting AI agents

They need confidence before giving agents more permissions.

Primary needs:

audit logs

security events

policy enforcement

approvals

role-based access

## 6.4 AI researchers

They want to compare agent behavior.

Primary needs:

replay

run comparison

metrics

exports

evaluation suites

## 6.5 Platform engineers

They operate shared agent infrastructure.

Primary needs:

high-scale ingestion

multi-tenancy

tracing

APIs

retention policies

OpenTelemetry compatibility

# 7. Core Concepts and Vocabulary

A consistent domain model is critical.

## Workspace

Top-level account boundary.

Example:

```text
Acme Engineering
```

## Project

A logical agent application.

Example:

```text
Coding Agent
Customer Support Agent
Incident Response Agent
```

## Agent

A specific agent identity or agent type.

Example:

```text
planner
researcher
coder
reviewer
```

## Run

One complete execution initiated by a user, event, job, or another agent.

Example:

```text
Run #A82F1
```

## Trace

The complete causally-related execution tree.

A run may equal one trace for simple cases.

## Span

A timed operation within a trace.

Examples:

```text
LLM call
tool call
terminal command
sub-agent execution
database query
```

## Event

An immutable point-in-time record.

Examples:

```text
file_read
file_write
tool_start
tool_end
retry
error
approval_requested
security_alert
```

## Artifact

A large object produced by a run.

Examples:

terminal logs

Git diffs

screenshots

files

model prompts

model outputs

## Evaluation

A score or judgment applied to a run.

Examples:

tests passed

task completed

latency threshold

code quality

human rating

## Policy

A rule controlling agent behavior.

Examples:

```text
deny filesystem/.env
require approval for git.push
deny database.drop_table
```

# 8. Primary User Experience

A developer should be able to install Agent Black Box with minimal code.

Example:

```text
pip install agent-blackbox
```

Then:

```text
from blackbox import observe

@observe
def run_agent():
    agent.run()
```

Or:

```text
from blackbox import AgentTracer

tracer = AgentTracer(
    project="coding-agent",
    api_key="..."
)

with tracer.run("Fix authentication bug"):
    agent.execute()
```

The developer runs the agent and opens the dashboard.

They immediately see:

```text
LIVE RUN

Coding Agent #124

● Planning
● Reading repository
● Calling Claude
● Modifying auth.py
● Running tests
○ Waiting...
```

The key UX promise:

Agent Black Box should make an opaque agent execution feel understandable.

# 9. Core Features

These are the foundational capabilities.

## 9.1 Run Timeline

A chronological timeline of everything the agent did.

Example:

```text
00:00  User request
00:01  Planning
00:03  File read
00:05  LLM call
00:11  Tool call
00:13  File modified
00:18  Test execution
00:24  Failure
00:26  Retry
00:39  Success
```

Filters:

LLM calls

tools

files

shell

network

database

errors

retries

security

## 9.2 Live Runs

Events appear in real time while the agent is running.

Useful for:

debugging

demos

production monitoring

watching long-running agents

## 9.3 LLM Call Tracking

Capture:

provider

model

latency

input tokens

output tokens

total tokens

estimated cost

prompt metadata

response metadata

status

retry count

cache use

Potentially store full prompt/response only when configured.

## 9.4 Tool Call Tracking

Capture:

tool name

operation

arguments

result

latency

success/failure

error

permissions used

Example:

```text
GitHub.searchIssues
Latency: 1.2 sec
Result count: 8
Status: success
```

## 9.5 Error Tracking

Show:

error type

message

originating span

stack trace

related previous events

retry behavior

## 9.6 Retry Tracking

Display retries as a first-class concept.

Example:

```text
Attempt 1
    ↓
Tool timeout
    ↓
Retry 1
    ↓
Model changed strategy
    ↓
Success
```

## 9.7 Run Summary

At the top of every run:

```text
Duration: 47 sec
Cost: $0.18
Tool calls: 12
LLM calls: 4
Files changed: 2
Retries: 1
Errors: 1
Status: SUCCESS
```

## 9.8 Run Status

Suggested statuses:

```text
QUEUED
RUNNING
WAITING
WAITING_FOR_APPROVAL
SUCCESS
FAILED
CANCELLED
TIMED_OUT
BLOCKED
```

# 10. Additional Features

These build on the core system.

## 10.1 Cost dashboards

Show total AI spend by:

workspace

project

agent

model

provider

user

day

run

## 10.2 Agent health score

Example:

```text
Reliability       94
Latency           82
Cost Efficiency   61
Tool Reliability  88
Loop Detection    100

Overall           85/100
```

The score should be explainable rather than magical.

## 10.3 Run comparison

Compare two or more runs.

## 10.4 Model comparison

Run the same task across models.

## 10.5 Replay

Step through historical runs event by event.

## 10.6 Fork / branch

Clone a historical run and modify:

model

prompt

temperature

tool permissions

agent version

## 10.7 Evaluation

Automatically score runs.

## 10.8 Security alerts

Detect risky behavior.

## 10.9 Approval gates

Require human confirmation before sensitive actions.

## 10.10 Search

Search historical activity.

Examples:

```text
all runs where npm test failed
all runs costing > $1
all runs touching auth.ts
all database write operations
all runs with > 3 retries
```

# 11. Detailed UX Examples

# 11.1 Main dashboard

```text
┌──────────────────────────────────────────────────────────────┐
│ AGENT BLACK BOX                                             │
├──────────────────────────────────────────────────────────────┤
│ Workspace: Personal                                         │
│ Project: Coding Agent                                       │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│ Runs today                 1,284                              │
│ Success rate               94.2%                              │
│ Total cost                 $83.17                             │
│ Avg latency                31.8s                              │
│ Failed runs                74                                 │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│ RECENT RUNS                                                   │
│                                                              │
│ #1482 Fix OAuth bug          SUCCESS   42s   $0.31            │
│ #1481 Refactor cache         FAILED    91s   $1.12            │
│ #1480 Add endpoint           SUCCESS   28s   $0.18            │
│ #1479 Fix flaky test         RUNNING   19s   $0.09            │
└──────────────────────────────────────────────────────────────┘
```

# 11.2 Run detail

```text
┌───────────────────────────────────────────────────────┐
│ Agent Run: Fix OAuth timeout                  SUCCESS │
│ 42.7 sec     $0.31     18 tool calls     2 retries    │
├───────────────────────────────────────────────────────┤
│                                                       │
│ USER                                                  │
│ ● Fix OAuth login timeout                             │
│ │                                                     │
│ ├── 🧠 LLM  Analyze repository          2.1 sec       │
│ │                                                     │
│ ├── 📄 READ src/auth/oauth.ts            40 ms        │
│ │                                                     │
│ ├── 🔧 TOOL GitHub Search Issues        1.3 sec       │
│ │                                                     │
│ ├── 🧠 LLM  Determine likely cause      3.8 sec       │
│ │                                                     │
│ ├── ✏ EDIT src/auth/oauth.ts                          │
│ │                                                     │
│ ├── 💻 npm test                         8.2 sec       │
│ │       ❌ 2 failures                                 │
│ │                                                     │
│ ├── 🔁 RETRY                                          │
│ │                                                     │
│ ├── ✏ EDIT src/auth/oauth.ts                          │
│ │                                                     │
│ └── 💻 npm test                         7.9 sec       │
│         ✅ 188 tests passed                           │
│                                                       │
└───────────────────────────────────────────────────────┘
```

# 11.3 LLM event drawer

```text
MODEL
Claude

Latency
2.41 sec

Tokens
Input:  8,421
Output: 1,208

Estimated Cost
$0.063

Prompt
[expand]

Response
[expand]

Metadata
temperature: 0.2
max_tokens: 4096
```

# 11.4 Code diff drawer

```text
- timeout = 5000
+ timeout = await getOAuthTimeout(provider)
```

Metadata:

```text
File: src/auth/oauth.ts
Lines added: 1
Lines removed: 1
Agent: coding-agent
Span: 01J9...
```

# 11.5 Tool drawer

```text
Tool
PostgreSQL

Operation
query

Query
SELECT id, token
FROM sessions
WHERE user_id = $1

Rows returned
1

Latency
18 ms

Status
SUCCESS
```

# 11.6 Failure analysis

```text
Failure detected

Primary failure:
npm test returned exit code 1

Likely originating event:
src/auth/oauth.ts modified at 00:13

Affected tests:
oauth_refresh_test
oauth_timeout_test

Agent response:
Retry initiated after 1.2 sec
```

PART

CORE ARCHITECTURE

System structure, storage, event model, SDKs, runtime behavior, and scaling seams.

# 12. System Architecture

Figure 1. High-level Agent Black Box architecture.

The platform should be divided into clear responsibilities.

```text
                    AGENT APPLICATION
                          │
                          │
                    Black Box SDK
                          │
                          ▼
                    Ingestion API
                          │
            ┌─────────────┼─────────────┐
            │             │             │
            ▼             ▼             ▼
      Event Storage   Live Stream    Job Queue
            │             │             │
            │             ▼             ▼
            │         Dashboard      Processors
            │                           │
            └──────────────┬────────────┘
                           ▼
                       Analytics
                           │
                  ┌────────┼─────────┐
                  ▼        ▼         ▼
                Cost     Errors    Security
```

Important architectural boundary:

```text
Collector / SDK
       ↓
Canonical Event Model
       ↓
Ingestion
       ↓
Processing
       ↓
Storage
       ↓
Query API
       ↓
Frontend
```

This boundary is what allows the project to grow.

# 13. MVP Architecture

Do not start with an overengineered distributed system.

Recommended MVP:

```text
Python SDK
    ↓
FastAPI
    ↓
PostgreSQL
    ↓
WebSocket / SSE
    ↓
Next.js
```

Optional:

```text
Redis
```

only if needed for:

pub/sub

rate limiting

caching

background job coordination

### MVP stack

SDK

Python

Backend

FastAPI

SQLAlchemy or SQLModel

Pydantic

Database

PostgreSQL

Frontend

Next.js

React

TypeScript

Streaming

WebSockets or Server-Sent Events

Local development

Docker Compose

# 14. Scale-Out Architecture

Figure 2. Scale-out architecture after durable event streaming and analytical storage are justified.

When usage grows:

```text
                            Agents
                              │
                     SDK / OTEL Exporter
                              │
                              ▼
                     Load Balancer / API
                              │
                              ▼
                       Ingestion Layer
                              │
                              ▼
                            Kafka
              ┌───────────────┼────────────────┐
              ▼               ▼                ▼
       Trace Processor   Cost Processor   Security Processor
              │               │                │
              └───────────────┼────────────────┘
                              ▼
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
      PostgreSQL          ClickHouse         Object Storage
       metadata            analytics          large payloads
          │                   │                   │
          └───────────────────┼───────────────────┘
                              ▼
                          Query API
                              │
                      WebSocket Gateway
                              │
                              ▼
                           Next.js
```

### Suggested roles

PostgreSQL:

users

workspaces

projects

runs

policies

metadata

ClickHouse:

event analytics

high-volume spans

aggregate queries

time-series analytics

Object storage:

prompts

responses

screenshots

large terminal logs

code artifacts

diffs

Kafka:

decouple ingestion from processors

absorb traffic spikes

multiple independent consumers

replay event streams

Kubernetes:

only when deployment complexity justifies it

autoscaling workers

isolating services

horizontal scaling

# 15. Event Model

All integrations should emit a canonical event format.

Example:

```text
{
  "event_id": "evt_01J...",
  "schema_version": "1.0",
  "workspace_id": "ws_123",
  "project_id": "proj_123",
  "run_id": "run_123",
  "trace_id": "trace_123",
  "span_id": "span_123",
  "parent_span_id": "span_parent",
  "agent_id": "coding-agent",
  "event_type": "tool_call_completed",
  "timestamp": "2026-10-06T20:13:22.029Z",
  "sequence": 42,
  "status": "success",
  "duration_ms": 843,
  "attributes": {
    "tool.name": "github",
    "tool.operation": "search_issues"
  },
  "payload": {},
  "tags": ["production", "coding-agent"]
}
```

## Event categories

### Run events

```text
run_started
run_completed
run_failed
run_cancelled
```

### Agent events

```text
agent_started
agent_completed
agent_state_changed
agent_spawned
```

### LLM events

```text
llm_request_started
llm_request_completed
llm_request_failed
```

### Tool events

```text
tool_call_started
tool_call_completed
tool_call_failed
```

### File events

```text
file_read
file_created
file_modified
file_deleted
```

### Git events

```text
git_diff
git_commit
git_branch_created
git_push
```

### Terminal events

```text
shell_command_started
shell_command_completed
shell_command_failed
```

### Database events

```text
db_query_started
db_query_completed
db_query_failed
```

### Network events

```text
http_request
http_response
```

### Reliability events

```text
retry
timeout
loop_detected
rate_limit
```

### Security events

```text
policy_warning
policy_block
secret_detected
approval_requested
approval_granted
approval_denied
```

# 16. Tracing Model

Agent Black Box should borrow heavily from distributed tracing.

A run contains a trace.

A trace contains spans.

Spans can contain child spans.

Example:

```text
Trace
 ├── Span: Planner
 │
 ├── Span: ResearchAgent
 │    ├── MCP.search
 │    └── GitHub.read
 │
 ├── Span: CodingAgent
 │    ├── file.read
 │    ├── file.write
 │    └── terminal
 │
 └── Span: ReviewerAgent
      └── test.run
```

## Span fields

```text
span_id
trace_id
parent_span_id
name
type
start_time
end_time
duration_ms
status
agent_id
attributes
error
```

## Trace waterfall

```text
Planner       ██████████████████████████████
Research        █████████
Search            ███
GitHub                ████
Coding                    ███████████████
LLM                         ████
Terminal                         ███████
Review                                  ███████
```

This should eventually become one of the product's strongest visualizations.

# 17. Python SDK Design

Figure 3. SDK layers and the non-blocking export path.

The SDK should feel invisible.

## Basic API

```text
from blackbox import AgentTracer

tracer = AgentTracer(
    api_key="...",
    project="coding-agent"
)

with tracer.run(name="Fix login bug"):
    agent.run()
```

## Decorator API

```text
from blackbox import observe

@observe
def run_agent():
    return agent.run()
```

## Manual spans

```text
with tracer.span("search-github", span_type="tool"):
    github.search_issues(...)
```

## LLM helper

```text
with tracer.llm_call(
    provider="anthropic",
    model="claude"
) as call:
    response = client.messages.create(...)
    call.record_usage(
        input_tokens=1234,
        output_tokens=432
    )
```

## Custom event

```text
tracer.event(
    "cache_hit",
    attributes={
        "key": "repo-index-v4"
    }
)
```

## SDK requirements

The SDK should support:

async APIs

context propagation

thread safety

local buffering

batching

retry

configurable sampling

redaction

disabled mode

offline development mode

# 18. Framework Integrations

Integrations should live outside the core event pipeline.

Each adapter converts framework-specific callbacks into canonical events.

Suggested directory:

```text
integrations/
├── langgraph/
├── openai/
├── anthropic/
├── mcp/
├── crewai/
├── autogen/
└── custom/
```

## LangGraph example

Goal:

```text
graph.compile(
    callbacks=[BlackBoxCallback()]
)
```

The adapter can map:

```text
node_started
node_completed
tool_called
model_called
state_changed
```

into canonical events.

# 19. Backend Services

Initially one FastAPI application can host several logical modules.

Later they can be separated.

## Ingestion API

Responsibilities:

authenticate SDK

validate schema

accept batches

attach server timestamp

enforce size limits

persist event

publish live update

## Run service

Responsibilities:

create runs

update status

calculate summary

retrieve run detail

## Query service

Responsibilities:

filtering

search

pagination

event retrieval

## Analytics service

Responsibilities:

aggregate costs

success rate

latency

model metrics

agent metrics

## Evaluation service

Responsibilities:

calculate scores

invoke evaluators

store results

## Policy service

Future responsibility:

evaluate action against security policy

return allow / deny / approval-required

# 20. Database Design

The database should separate metadata from high-volume events.

## Initial PostgreSQL tables

### users

```text
id
email
name
created_at
```

### workspaces

```text
id
name
created_at
```

### workspace_members

```text
workspace_id
user_id
role
```

### projects

```text
id
workspace_id
name
slug
created_at
```

### agents

```text
id
project_id
name
version
metadata
```

### runs

```text
id
project_id
agent_id
trace_id
name
status
started_at
completed_at
duration_ms
total_cost
total_tokens
tool_call_count
llm_call_count
retry_count
error_count
metadata
```

### spans

```text
id
run_id
trace_id
parent_span_id
name
type
status
started_at
completed_at
duration_ms
attributes
```

### events

```text
id
run_id
trace_id
span_id
event_type
sequence
timestamp
status
attributes
payload
```

### artifacts

```text
id
run_id
span_id
artifact_type
storage_uri
size_bytes
metadata
```

### evaluations

```text
id
run_id
evaluator_name
score
label
metadata
created_at
```

### policies

```text
id
workspace_id
name
rule
action
enabled
```

# 21. Live Streaming

Live traces are central to the product.

Possible implementations:

### MVP

```text
API receives event
    ↓
store in PostgreSQL
    ↓
publish event to WebSocket connection
    ↓
frontend appends event
```

### Scale

```text
SDK
 ↓
Ingestion
 ↓
Kafka
 ├── persistence consumer
 ├── analytics consumer
 └── WebSocket consumer
          ↓
      Redis Pub/Sub
          ↓
    WebSocket gateways
```

The frontend should handle:

out-of-order events

reconnects

duplicate events

missing events

stale connections

Use immutable event IDs and sequence numbers.

PART

INTELLIGENCE & AGENT FEATURES

Analytics, cost, failures, multi-agent tracing, replay, evaluation, and security capabilities.

# 22. Analytics Engine

Analytics should be built from structured events.

Example metrics:

### Reliability

```text
run success rate
tool success rate
model request failure rate
timeout rate
retry rate
```

### Performance

```text
run latency
LLM latency
tool latency
time waiting for approval
```

### Cost

```text
cost per run
cost per successful run
cost by agent
cost by model
cost by tool
```

### Behavior

```text
average tool calls per run
average LLM calls
average retries
average files modified
```

### Quality

```text
evaluation score
tests passed
human rating
task completion
```

# 23. Cost Observability

A major product capability.

Example dashboard:

```text
Today's Agent Spend
$183.72

Claude            $91.13
GPT               $62.04
Gemini            $19.22
Embeddings        $11.33
```

By agent:

```text
Support Agent       $72
Coding Agent        $61
Research Agent      $34
Onboarding Agent    $16
```

Most expensive runs:

```text
#1382    $8.12
#1221    $5.91
#1402    $4.88
```

## Cost engine

Maintain provider pricing metadata:

```text
{
  "provider": "example",
  "model": "model-x",
  "input_per_million": 3.0,
  "output_per_million": 15.0
}
```

Cost:

```text
input_cost
+ output_cost
+ cached_token_cost
+ tool-specific cost
= run cost
```

The pricing table should be versioned because model pricing changes.

# 24. Failure and Retry Analysis

Agent failures should be represented causally.

Example:

```text
file modification
      ↓
test failure
      ↓
agent retry
      ↓
different modification
      ↓
success
```

Useful metrics:

```text
failure origin
retry count
retry success rate
time spent retrying
cost spent retrying
repeated actions
```

Potential UI:

```text
Run cost: $0.91

Initial attempt: $0.24
Retries:         $0.67

⚠ 74% of cost came from retries.
```

# 25. Loop and Anomaly Detection

Start with deterministic heuristics.

## Repeated tool loop

Detect:

```text
search()
read()
reason()
search()
read()
reason()
search()
read()
reason()
```

Possible rule:

```text
same tool sequence repeated >= 3 times
within <= 60 seconds
```

Alert:

```text
⚠ Possible execution loop

The agent repeated:

search → read → reason

11 times in 37 seconds.
```

## Repeated prompt loop

Hash normalized prompts and compare.

## Retry storm

```text
retry_count > threshold
```

## Token explosion

```text
context_size increases rapidly each turn
```

## Cost spike

```text
run cost > historical p99
```

## Tool failure cascade

```text
database.query failed
        ↓
retry
        ↓
database.query failed
        ↓
alternate tool
        ↓
bad output
```

These detectors should be implemented as plugins so more can be added later.

# 26. File, Git, and Code Diff Tracking

For coding agents, this feature is essential.

Track:

file reads

file writes

created files

deleted files

Git diffs

commits

branches

pushes

Example event:

```text
{
  "event_type": "file_modified",
  "path": "src/auth/session.ts",
  "diff_artifact_id": "artifact_123"
}
```

UI:

```text
- if (token)
+ if (token && !isExpired(token))
```

Potential analytics:

```text
files touched
lines added
lines deleted
test files modified
source files modified
```

Security use:

```text
⚠ Agent modified CI/CD configuration
⚠ Agent changed authentication code
⚠ Agent edited dependency lock file
```

# 27. Terminal and Tool Tracking

Shell commands should be first-class events.

Example:

```text
Command
npm test

Working directory
/repo

Duration
8.2 sec

Exit code
1

Output
[expand]
```

Sensitive environment variables should never be automatically captured.

Commands may be classified:

```text
READ_ONLY
MODIFY_FILES
NETWORK
PACKAGE_INSTALL
PROCESS_CONTROL
DESTRUCTIVE
```

This classification later powers security policies.

# 28. Multi-Agent Observability

Figure 4. Multi-agent trace hierarchy represented as distributed tracing.

Multi-agent systems should look like distributed systems.

Example:

```text
Main Agent
│
├── Research Agent
│      ├── Web Search
│      └── GitHub
│
├── Coding Agent
│      ├── filesystem
│      ├── terminal
│      └── Git
│
└── Review Agent
       ├── Git diff
       └── tests
```

Each child agent receives:

```text
trace_id = inherited
parent_span_id = spawning agent span
agent_id = child identity
```

UI should support both:

chronological timeline

hierarchical agent tree

# 29. Replay

Replay should initially mean visual replay, not re-execution.

## Visual replay

Timeline playback:

```text
▶ 00:00 User request
▶ 00:02 Agent planning
▶ 00:05 Tool call
▶ 00:08 File modified
▶ 00:12 Command executed
▶ 00:18 Failure
```

Controls:

```text
play
pause
1x
2x
5x
step forward
step backward
```

## Deterministic replay

Later, capture enough data to reconstruct:

model request

tool input

tool result

state transition

This allows local simulation without re-running external systems.

## Execution replay

Advanced:

Re-execute using:

same model

new model

new prompt

new agent version

This requires sandboxing and should be a later feature.

# 30. Run Comparison

Users select:

```text
Run A
Run B
```

Overview:

```text
          RUN A             RUN B

Model     Claude            GPT
Time      38 sec            51 sec
Cost      $0.24             $0.18
Calls     14                19
Retries   0                 2
Tests     188/188           188/188
```

Execution divergence:

```text
                    START
                      │
            ┌─────────┴─────────┐
            ▼                   ▼

        Run A                Run B
        inspected            searched
        auth.ts              repo
            │                   │
        modified             inspected
        session.ts           4 files
            │                   │
         tests                retry
            │                   │
       SUCCESS               SUCCESS
```

Comparison dimensions:

models

prompts

agent version

tool sequence

files changed

costs

latency

success

evaluation score

# 31. Evaluation Platform

Observability naturally creates evaluation infrastructure.

## Evaluation types

### Deterministic

Examples:

```text
tests passed
JSON schema valid
expected file exists
exit code == 0
```

### Metric

Examples:

```text
cost < $0.25
latency < 30 seconds
retries <= 2
```

### LLM judge

Examples:

```text
answer correctness
code quality
instruction following
```

### Human rating

Examples:

```text
thumbs up
thumbs down
1-5 score
comment
```

## Evaluation dashboard

```text
Agent v12

Success rate      92%
Avg cost          $0.21
Avg latency       31s
Eval score        8.4/10
Regression rate   2.1%
```

This allows Agent Black Box to become a lightweight agent experimentation platform.

# 32. Security Layer

Figure 5. Future policy enforcement and human-approval control path.

Long-term, the system should move from observation to intervention.

Examples:

```text
READ .env
```

Alert:

```text
🚨 SECURITY EVENT

Agent attempted access to:
.env

Possible secrets exposure.
```

Another:

```text
rm -rf ./production-data
```

Response:

```text
🚨 Destructive command detected
Action blocked by policy
```

Security detection categories:

secrets access

credential exposure

destructive shell commands

privileged database writes

unexpected network domains

suspicious downloads

prompt injection indicators

unusual tool usage

excessive permission requests

# 33. Policy and Approval System

Policies should use a flexible rule engine.

Example:

```text
filesystem.src/*     ALLOW
filesystem/.env      DENY
github.read          ALLOW
github.create_pr     ALLOW
github.merge         REQUIRE_APPROVAL
database.read        ALLOW
database.delete      DENY
shell.rm_recursive   DENY
```

Potential YAML:

```text
policies:
  - match:
      resource: "filesystem/.env"
    action: deny

  - match:
      tool: "github"
      operation: "merge"
    action: require_approval

  - match:
      tool: "database"
      operation: "delete"
    action: deny
```

Approval flow:

```text
Agent requests git.merge
       ↓
Policy engine
       ↓
REQUIRE_APPROVAL
       ↓
Human notification
       ↓
Approve / deny
       ↓
Agent continues or stops
```

# 34. Search and Investigation

Search should support structured filters.

Examples:

```text
status:failed
cost:>1
retries:>3
tool:postgres
file:"src/auth/*"
model:"claude*"
duration:>60s
security:true
```

Natural-language search can come later:

Show me failed runs that edited authentication code and then retried more than twice.

# 35. Alerts

Possible alerts:

```text
run failure rate > 10%
agent cost exceeds $50/day
security policy blocked action
retry rate suddenly increases
tool latency p95 > 5 sec
specific agent version regresses
```

Destinations:

email

Slack

webhook

PagerDuty

Teams

PART

PLATFORM, PRIVACY & OPERATIONS

Tenancy, access control, retention, extensions, APIs, reliability, and project operations.

# 36. Projects, Workspaces, and Multi-Tenancy

Design for multi-tenancy from the beginning.

Hierarchy:

```text
User
 └── Workspace
      ├── Project
      │    ├── Agent
      │    └── Runs
      └── Project
```

Every database query should be scoped by workspace.

Multi-tenant requirements:

tenant-aware indexes

API authorization

encrypted secrets

usage limits

per-workspace retention

per-workspace policy

# 37. Authentication and RBAC

Possible roles:

```text
OWNER
ADMIN
DEVELOPER
VIEWER
SECURITY
```

Permissions:

```text
view runs
view prompts
view sensitive payloads
create API keys
edit policies
approve agent actions
delete data
manage members
```

SDK authentication should use project API keys.

Dashboard authentication can use:

OAuth

Google

GitHub

# 38. Privacy and Data Handling

Agent traces may contain sensitive information.

The system should support:

## Redaction

SDK-side redaction is best.

Example configuration:

```text
tracer = AgentTracer(
    redact=[
        "OPENAI_API_KEY",
        "DATABASE_PASSWORD",
        "*.token"
    ]
)
```

## Payload modes

```text
FULL
METADATA_ONLY
DISABLED
```

For LLM calls:

```text
FULL:
store prompts and responses

METADATA_ONLY:
store model, tokens, cost, latency

DISABLED:
do not trace LLM content
```

## Secret detection

Never intentionally persist:

API keys

passwords

credentials

private keys

unless the user explicitly configures secure capture.

# 39. Retention and Storage

Figure 6. Separation of transactional metadata, analytical telemetry, and large artifacts.

Different data has different retention requirements.

Example:

```text
run metadata       1 year
events             90 days
raw prompts        30 days
terminal output    30 days
artifacts           configurable
```

Retention should be configurable per workspace.

Cold storage can move historical data to cheaper object storage.

# 40. Plugin and Extension Architecture

This is critical for future scalability.

Define interfaces for:

## Event sources

```text
class EventAdapter:
    def convert(self, raw_event) -> list[CanonicalEvent]:
        ...
```

## Processors

```text
class EventProcessor:
    def process(self, event: CanonicalEvent) -> None:
        ...
```

Examples:

cost processor

loop detector

security detector

## Evaluators

```text
class Evaluator:
    def evaluate(self, run) -> EvaluationResult:
        ...
```

## Exporters

```text
class Exporter:
    def export(self, events):
        ...
```

Examples:

Agent Black Box cloud

local JSON

OpenTelemetry

custom webhook

## Storage adapters

```text
class EventStore:
    def write(self, events):
        ...

    def query(self, filters):
        ...
```

Possible future implementations:

PostgreSQL

ClickHouse

local SQLite

Elasticsearch

# 41. Public API

A future public API can expose:

```text
POST /v1/events
POST /v1/events/batch
POST /v1/runs
GET  /v1/runs
GET  /v1/runs/{id}
GET  /v1/runs/{id}/events
GET  /v1/runs/{id}/spans
GET  /v1/runs/{id}/artifacts
POST /v1/runs/{id}/evaluations
GET  /v1/analytics
GET  /v1/projects
POST /v1/policies
```

Use versioned APIs from the beginning:

```text
/v1
```

# 42. OpenTelemetry Compatibility

Agent Black Box should avoid inventing tracing concepts unnecessarily.

A strong future direction is to support OpenTelemetry export/import.

Potential mappings:

```text
Agent Black Box trace_id → OTEL trace_id
span → OTEL span
attributes → OTEL attributes
events → span events
```

Benefits:

interoperability

familiarity

exporting to existing observability systems

importing infrastructure traces

Long-term:

```text
agent trace
   +
HTTP trace
   +
database trace
   +
Kubernetes trace
```

could become one connected execution view.

# 43. Observability for Agent Black Box Itself

The observability platform itself must be observable.

Metrics:

```text
events_ingested_total
events_rejected_total
ingestion_latency_ms
queue_depth
processor_lag
websocket_connections
db_query_latency
event_storage_failures
```

Use:

structured logs

traces

metrics

health endpoints

# 44. Reliability and Fault Tolerance

## SDK guarantees

Tracing should never crash the agent.

If the backend is unavailable:

```text
agent continues
events buffer locally
export retries later
```

## Ingestion guarantees

At-least-once delivery is acceptable initially.

Deduplicate using:

```text
event_id
```

## Ordering

Each run should include:

```text
sequence number
timestamp
event_id
```

Do not assume network arrival order equals execution order.

## Idempotency

Repeated event submission should not create duplicates.

# 45. Scalability Strategy

Design around dimensions that may grow independently.

## Number of users

Solution:

stateless API servers

load balancer

horizontal scaling

## Events per second

Solution:

batch SDK exports

Kafka

partitioning by workspace/project/run

## Historical event volume

Solution:

ClickHouse

partitioning by date/workspace

retention policies

## Large artifacts

Solution:

object storage

signed URLs

do not keep giant payloads in PostgreSQL

## WebSocket connections

Solution:

dedicated gateways

Redis Pub/Sub

sticky sessions if needed

## Heavy analytics

Solution:

precomputed aggregates

materialized views

ClickHouse

## Noisy tenants

Solution:

quotas

rate limits

per-tenant partitions

usage accounting

PART

DELIVERY & PRODUCTIZATION

Repository design, testing, milestones, demo strategy, positioning, and future backlog.

# 46. Repository Structure

A scalable monorepo layout:

```text
agent-black-box/
│
├── apps/
│   ├── web/
│   └── api/
│
├── packages/
│   ├── sdk-python/
│   ├── sdk-typescript/
│   ├── event-schema/
│   ├── ui/
│   └── shared/
│
├── services/
│   ├── ingestion/
│   ├── analytics/
│   ├── evaluator/
│   └── policy/
│
├── integrations/
│   ├── langgraph/
│   ├── openai/
│   ├── anthropic/
│   └── mcp/
│
├── processors/
│   ├── cost/
│   ├── retries/
│   ├── loops/
│   └── security/
│
├── examples/
│   ├── coding-agent/
│   ├── research-agent/
│   └── multi-agent/
│
├── infrastructure/
│   ├── docker/
│   ├── terraform/
│   └── kubernetes/
│
└── docs/
```

For the MVP, several of these directories can contain placeholder interfaces rather than full services.

# 47. Testing Strategy

## SDK

unit tests

async context tests

batching tests

retry tests

redaction tests

## API

schema validation

auth

rate limiting

event idempotency

multi-tenant isolation

## Database

migrations

indexing

pagination

concurrent inserts

## Frontend

timeline rendering

event ordering

reconnect behavior

filtering

## End-to-end

Example:

```text
demo agent
 ↓
SDK
 ↓
API
 ↓
database
 ↓
WebSocket
 ↓
frontend
```

Verify the final run is displayed correctly.

## Load testing

Later:

```text
10K events/sec
100K events/sec
1M stored runs
```

# 48. Development Phases

Figure 7. Delivery sequence from foundation to control-plane capabilities.

# Phase 0 — Product skeleton

Build:

repository

basic Next.js UI

FastAPI backend

PostgreSQL

Docker Compose

canonical event schema

Goal:

```text
frontend → API → database
```

# Phase 1 — Core tracing MVP

Build:

Python SDK

run creation

custom events

LLM spans

tool spans

run timeline

run summary

WebSocket live updates

Goal:

A developer can trace one agent run.

# Phase 2 — Coding agent observability

Add:

file events

code diffs

shell commands

Git operations

terminal output

Goal:

Make the first public demo visually impressive.

# Phase 3 — Analytics

Add:

dashboard

cost tracking

latency charts

success rate

tool statistics

model statistics

# Phase 4 — Multi-agent tracing

Add:

parent/child agents

trace hierarchy

waterfall view

agent tree

# Phase 5 — Reliability intelligence

Add:

loop detection

retry analysis

failure chains

cost anomalies

health score

# Phase 6 — Evaluations

Add:

deterministic evaluators

test-based evaluations

human ratings

run comparison

# Phase 7 — Replay

Add:

visual replay

run branching

model comparison

controlled re-execution

# Phase 8 — Security

Add:

security detectors

secret warnings

policy engine

approval gates

command classification

# Phase 9 — Scale

Only when necessary:

Kafka

ClickHouse

object storage

Kubernetes

dedicated workers

dedicated WebSocket gateways

# 49. MVP Definition

The MVP should do exactly three things extremely well:

## 1. Live execution traces

Watch an agent run in real time.

## 2. Beautiful per-run timelines

Understand the entire execution in seconds.

## 3. Cost / tool / error analytics

Understand:

what happened

how long it took

how much it cost

where it failed

The first version does not need:

complex security

enterprise RBAC

Kafka

Kubernetes

replay execution

ClickHouse

AI-generated root-cause analysis

# 50. Demo Agent

Figure 8. Flagship coding-agent demonstration flow.

Build a dedicated coding agent for demonstrations.

Input:

Fix this broken repository.

Agent capabilities:

```text
read files
search repository
call LLM
edit files
run tests
retry
```

The repository should contain an intentional bug.

Example:

```text
OAuth session expiration bug
```

Demo flow:

```text
read auth.ts
read session.ts
LLM reasoning
edit session.ts
run tests
tests fail
retry
edit session.ts
run tests
tests pass
```

This produces a visually rich trace.

# 51. LinkedIn Demo Strategy

The product should be understandable in a 20-30 second video.

## Video layout

Left:

```text
AI CODING AGENT
```

Right:

```text
AGENT BLACK BOX
```

As the coding agent works, Agent Black Box updates:

```text
READ package.json

READ auth.ts

LLM 3,124 tokens

EDIT auth.ts

RUN npm test

❌ FAILED

RETRY

EDIT session.ts

RUN npm test

✅ PASSED
```

End screen:

```text
Run complete

42 seconds
$0.17
12 tool calls
2 files modified
1 failed attempt
142 tests passed
```

## Suggested post hook

AI agents can now write code, execute shell commands, query databases, and interact with APIs. But debugging what they actually did is still painful.

Then:

So I built Agent Black Box — a flight recorder for AI agents.

The demo should explain itself without requiring a long caption.

# 52. Resume Positioning

Possible future bullet style:

Built an observability platform for AI agents that captures distributed execution traces across LLM calls, tool usage, file changes, shell commands, retries, and multi-agent workflows, with real-time dashboards and cost/error analytics.

A stronger quantified version should come later after real usage.

Potential technology line:

```text
Python, FastAPI, Next.js, PostgreSQL, WebSockets, OpenTelemetry
```

Later:

```text
Kafka, ClickHouse, Kubernetes
```

only if actually implemented.

# 53. Future Feature Backlog

This section intentionally contains ideas that do not need to be implemented immediately.

## Developer experience

TypeScript SDK

Go SDK

CLI

local desktop viewer

VS Code extension

GitHub app

framework auto-instrumentation

## Timeline

event bookmarks

comments

shareable trace links

collapsing repeated spans

custom views

## AI-assisted debugging

summarize run

identify likely failure origin

explain retry behavior

suggest optimization

suggest cheaper model

identify unnecessary context

## Cost

budgets

spend alerts

per-user attribution

per-customer cost attribution

cost forecasting

## Reliability

agent SLOs

regression detection

version comparisons

flaky-tool detection

tool dependency health

## Evaluation

benchmark suites

prompt experiments

model experiments

agent version experiments

dataset evaluation

regression gates in CI

## Security

prompt injection detection

data-loss prevention

policy simulator

risk scoring

sandbox integration

secret scanners

domain allow lists

command allow lists

## Production controls

pause agent

terminate run

require approval

modify permissions mid-run

rate-limit agent

set token budget

set tool-call budget

## Replay

state snapshots

deterministic tool replay

sandbox re-execution

compare alternate models

compare alternate prompts

## Collaboration

comments

annotations

run assignment

incident links

shared dashboards

run collections

## Integrations

Slack

PagerDuty

Datadog

Grafana

GitHub

GitLab

Sentry

Linear

Jira

## Data exports

JSON

CSV

Parquet

OTEL

webhooks

# 54. What Not to Build First

Avoid turning the MVP into a giant infrastructure project.

Do not start with:

ten microservices

Kubernetes

Kafka

ClickHouse

distributed policy engines

enterprise billing

dozens of agent integrations

complex AI root-cause analysis

Why?

Because none of those prove that people actually like the main product.

The MVP must prove one thing:

Developers find it useful to visually understand agent executions.

Only then increase infrastructure complexity.

# 55. Technical Decision Principles

When choosing between technologies, ask:

## Does this solve a current problem?

Do not add Kafka because it looks impressive.

Add Kafka when:

```text
ingestion throughput
or
processor independence
or
replayability

actually requires it.
```

## Can the component be replaced later?

Use interfaces around:

event storage

exporters

analyzers

model pricing

integrations

## Is the data model stable?

The canonical event schema is more important than the first database.

## Is the feature observable?

Every critical backend service should emit its own:

logs

metrics

traces

## Can it support multi-tenancy?

Do not bolt workspace isolation on later.

## Can large payloads be separated?

Store metadata in relational databases.

Store large artifacts separately.

## Can sensitive data be disabled?

Tracing should be configurable.

# 56. Final Product Vision

The full progression should look like:

```text
PHASE 1
Observe

"What did my agent do?"

        ↓

PHASE 2
Understand

"Why did it fail?"

        ↓

PHASE 3
Optimize

"Why was this run slow or expensive?"

        ↓

PHASE 4
Evaluate

"Which model / prompt / agent version works best?"

        ↓

PHASE 5
Secure

"Is this agent doing something dangerous?"

        ↓

PHASE 6
Control

"Should this action be allowed to happen?"
```

At maturity, Agent Black Box could sit underneath almost any autonomous workflow.

```text
                  AGENT BLACK BOX

                       │
       ┌───────────────┼────────────────┐
       │               │                │
       ▼               ▼                ▼
 Coding Agent    Research Agent    Support Agent
       │               │                │
       └───────────────┼────────────────┘
                       │
                       ▼
                 Trace Collection
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
     Observability   Evaluation   Security
          │            │            │
          └────────────┼────────────┘
                       ▼
                 Control Plane
```

The strongest version of the idea is not simply:

"a dashboard for agent logs."

It is:

The operational layer for understanding and controlling autonomous software.

That framing gives the project room to grow for a long time without forcing the MVP to be large.

# Recommended First Build

If development starts today, build in this order:

```text
1. Canonical event schema
2. FastAPI ingestion endpoint
3. PostgreSQL data model
4. Python SDK
5. Basic Next.js run list
6. Run detail timeline
7. WebSocket live events
8. LLM usage/cost tracking
9. Tool-call tracking
10. Demo coding agent
11. File/code diff tracking
12. Retry/error visualization
13. Dashboard metrics
```

Do not move to the next major architecture stage until the preceding product experience feels polished.

The first public version should make someone watch a run for ten seconds and immediately think:

"Oh — I can finally see what the agent is actually doing."

That is the product.

# Principal Engineering Addendum: Build-to-Production Design

This addendum turns the product specification above into an implementation and operations contract. The earlier sections describe what Agent Black Box is and how the experience should feel. The sections below define the decisions, invariants, failure semantics, APIs, data ownership, scaling triggers, security boundaries, testing strategy, operational practices, and delivery sequence required to build the system without losing architectural coherence as it grows.

The document should be treated as a living design record. Decisions marked Committed are the default implementation path until an Architecture Decision Record (ADR) changes them. Decisions marked Deferred intentionally remain open because choosing too early would create unnecessary complexity. Decisions marked Guardrail are invariants that new features must preserve.

PART

PRINCIPAL LOW-LEVEL DESIGN

Committed architecture decisions, invariants, contracts, schemas, and implementation-level semantics.

# 57. Executive Engineering Decisions

## 57.1 Committed choices

For the first production-capable version, the system will use the following technologies and boundaries:

Web application: Next.js with React and TypeScript, using the App Router.

Primary API and control plane: Python with FastAPI and Pydantic.

Primary transactional datastore: PostgreSQL.

Real-time delivery: Server-Sent Events for one-way run updates by default; WebSockets only for interactions that require bidirectional control such as approvals or live operator commands.

Background work: an internal job abstraction implemented with PostgreSQL-backed jobs initially; Redis-backed workers may be introduced when latency or concurrency makes database polling undesirable.

SDK v1: Python.

SDK v2: TypeScript.

Canonical telemetry format: Agent Black Box canonical events and spans, designed as a superset that can map to and from OpenTelemetry.

Large artifacts: object storage behind an ArtifactStore abstraction; local filesystem in development and S3-compatible storage in hosted environments.

Authentication: OAuth for dashboard users plus scoped project API keys for SDK ingestion.

Tenant boundary: workspace ID is mandatory on all persistent domain records and all authorization decisions.

Infrastructure packaging: Docker from day one. Docker Compose for local development. Kubernetes is not required for MVP.

## 57.2 Deferred choices

The following are intentionally deferred:

Kafka or another durable event bus.

ClickHouse or another columnar analytics store.

Kubernetes for production orchestration.

a dedicated Go ingestion service.

distributed policy evaluation.

multi-region active/active deployment.

enterprise SSO/SCIM.

full deterministic agent re-execution.

These are not rejected. They are scale-stage tools. The architecture must preserve seams that allow their introduction without changing the SDK contract or public event schema.

## 57.3 Why FastAPI first instead of Go

A dedicated Go ingestion path would be a reasonable high-scale end state, but it creates a second backend language and duplicated validation/authentication logic before the system has evidence that Python ingestion is the bottleneck. FastAPI provides strong typing through Pydantic, excellent async support, straightforward OpenAPI generation, and excellent compatibility with the Python agent ecosystem. The first constraint is product velocity and correctness, not raw request-per-second throughput.

The ingestion boundary therefore uses an implementation-neutral HTTP contract. If profiling later shows that validation, JSON decoding, or network concurrency is materially limiting throughput, the ingestion route can be reimplemented in Go while the FastAPI control plane remains unchanged.

Migration condition: consider a dedicated ingestion service when sustained traffic exceeds approximately 5,000 events/second per API deployment, p95 ingestion acknowledgement exceeds 150 ms under expected load, or CPU time spent in validation/serialization becomes a top-three service bottleneck after batching and compression are already enabled.

## 57.4 Why PostgreSQL first

PostgreSQL is the source of truth for transactional entities: workspaces, users, projects, agents, runs, API keys, policies, evaluation definitions, and summary metadata. It also stores raw events initially because operational simplicity is more valuable than introducing multiple databases prematurely.

Raw events are append-heavy and eventually become a poor fit for a transactional relational store at high scale. This is anticipated. The repository layer separates RunRepository, EventStore, ArtifactStore, and AnalyticsStore, so event storage can move independently.

Migration condition: introduce ClickHouse or another columnar event store when one or more are true:

stored telemetry exceeds roughly 250-500 million events;

common analytics queries scan millions of rows and consistently miss interactive latency goals;

event retention begins dominating PostgreSQL storage and vacuum/maintenance cost;

aggregate dashboard queries materially interfere with control-plane transactions;

ingestion write amplification from indexes becomes a meaningful bottleneck.

## 57.5 Why no Kafka in the MVP

The MVP does not have enough independent consumers or throughput to justify Kafka's operational cost. Durable ingestion can initially be achieved with idempotent HTTP batches and transactional writes. Processors can consume from a database outbox or job table.

Kafka becomes useful when ingestion must be decoupled from many independently scalable consumers such as storage, analytics, cost calculation, security analysis, alerting, evaluation, and live-stream fanout. At that point a durable replayable log is valuable, not decorative.

Migration condition: add Kafka when there are at least three independently scaled event consumers, ingestion spikes need buffering beyond what the API/database can safely absorb, or reprocessing historical event streams becomes a routine operational requirement.

## 57.6 Why OpenTelemetry compatibility, not OpenTelemetry dependency

OpenTelemetry should influence naming, trace relationships, context propagation, and exporter design. The product, however, needs agent-specific concepts that generic tracing does not always express cleanly: agent identity, tool permission decisions, replay metadata, prompt redaction state, evaluation results, token cost, model strategy changes, code diffs, and human approval states.

Therefore Agent Black Box owns a canonical schema and provides an OpenTelemetry mapping layer. This protects product semantics while allowing interoperability. Because generative-AI semantic conventions continue to evolve, the mapping must be versioned and isolated behind an adapter instead of leaking unstable external naming throughout the domain model.

# 58. Goals, Non-Goals, and Product Boundaries

## 58.1 Primary goals

The system must allow an engineer to answer the following questions quickly:

What did this agent do?

In what order did it do those things?

Which model and tool calls were responsible for most time and cost?

Where did an error originate?

How did the agent respond to the error?

What files, commands, APIs, and databases did it touch?

Did it repeat itself or enter a loop?

How does this run differ from another run?

Can the run be evaluated consistently?

Did the agent attempt anything risky?

Which policy decision allowed or blocked an action?

Can a team search historical runs to identify recurring patterns?

## 58.2 Secondary goals

Make the dashboard useful enough that developers leave it open during active agent development.

Make the integration lightweight enough that instrumentation feels comparable to adding error tracking.

Keep local-first development possible.

Support both single-agent and multi-agent workflows.

Support hosted and eventually self-hosted deployment models.

Provide enough data portability that users are never locked into proprietary trace storage.

## 58.3 Explicit non-goals for v1

Building an agent framework.

Replacing LangGraph, OpenAI Agents SDK, CrewAI, or similar orchestrators.

Serving LLM inference.

Becoming a general-purpose APM replacement.

Capturing hidden chain-of-thought from model providers.

Promising deterministic reproduction of nondeterministic model executions.

Running arbitrary untrusted customer code inside the Agent Black Box control plane.

Providing a universal sandbox in the first release.

## 58.4 Product boundary

Agent Black Box observes and controls the boundary around an agent. It does not own the agent's business logic. The SDK captures metadata and events; adapters translate framework callbacks; policy hooks can intercept actions when explicitly integrated.

This distinction is important because it keeps the product compatible with many agent architectures instead of requiring users to rewrite their agents inside a proprietary runtime.

# 59. Personas and Critical User Journeys

## 59.1 Solo developer

A developer is building a coding agent locally. They install the SDK, run the agent, and open the local dashboard. The primary journey is:

```text
instrument -> execute -> watch live trace -> inspect failure -> change code -> rerun -> compare
```

Success means the developer diagnoses a failure faster than reading raw terminal logs.

## 59.2 AI platform engineer

A platform engineer owns several production agents. Their journey is:

```text
open project dashboard -> notice reliability regression -> filter failed runs -> compare agent versions -> isolate failing tool -> create alert or rollback
```

Success means production regressions can be isolated using telemetry rather than reproducing every run manually.

## 59.3 Security engineer

The security user cares less about prompts and more about actions and permissions:

```text
view security events -> inspect blocked operation -> verify policy -> review prior similar attempts -> tune policy
```

Success means autonomous execution can be expanded without losing auditability.

## 59.4 Research/evaluation engineer

The evaluation user repeatedly runs datasets across models or agent versions:

```text
create experiment -> execute N runs -> collect deterministic and model-based scores -> compare quality/cost/latency -> select candidate
```

Success means experimental evidence is attached to exact traces instead of maintained in separate spreadsheets.

# 60. Functional Requirements

Requirements use the form FR-<domain>-<number> so they can be referenced in issues, tests, and ADRs.

## 60.1 Ingestion

FR-ING-001: accept single-event and batched-event ingestion.

FR-ING-002: authenticate every ingestion request using a project-scoped credential.

FR-ING-003: reject events with unsupported schema versions using a machine-readable error.

FR-ING-004: support gzip or equivalent HTTP compression for batches.

FR-ING-005: support idempotent retry using globally unique event IDs.

FR-ING-006: acknowledge accepted events only after they have reached the current durability boundary.

FR-ING-007: apply per-project payload size and rate limits.

FR-ING-008: never require the agent to wait for analytics processing before continuing.

## 60.2 Runs and traces

FR-RUN-001: create, update, complete, cancel, and fail a run.

FR-RUN-002: associate every span and event with exactly one workspace, project, and run.

FR-RUN-003: support nested spans and parent/child agent relationships.

FR-RUN-004: calculate summary counters without requiring clients to send them.

FR-RUN-005: tolerate late-arriving events after a run is marked complete.

FR-RUN-006: expose an immutable event timeline and derived summaries separately.

## 60.3 Live UX

FR-LIVE-001: a subscribed client receives newly accepted run events with low latency.

FR-LIVE-002: reconnecting clients can resume from a cursor/event ID.

FR-LIVE-003: duplicate delivery must not create duplicate rows in the UI.

FR-LIVE-004: the UI must communicate when it is showing partial data.

## 60.4 Cost

FR-COST-001: compute model cost from versioned provider pricing metadata.

FR-COST-002: preserve the pricing version used for historical calculations.

FR-COST-003: allow users to override or add custom model pricing.

FR-COST-004: distinguish estimated cost from provider-billed cost.

## 60.5 Security and privacy

FR-SEC-001: support SDK-side redaction before network transmission.

FR-SEC-002: support metadata-only capture for prompts/responses.

FR-SEC-003: audit policy changes and approval decisions.

FR-SEC-004: support deny and require-approval policy outcomes.

FR-SEC-005: prevent one workspace from querying another workspace's data.

## 60.6 Evaluation

FR-EVAL-001: attach zero or more evaluation results to a run.

FR-EVAL-002: preserve evaluator name, version, configuration, and timestamp.

FR-EVAL-003: support deterministic, human, and model-based evaluators.

FR-EVAL-004: allow experiment comparison across agent/model versions.

# 61. Non-Functional Requirements and SLOs

The following are target objectives, not promises to users until production measurements validate them.

## 61.1 Availability

For the hosted control plane after public beta:

dashboard/API monthly availability target: 99.9%;

ingestion monthly availability target: 99.95%;

telemetry processing can be eventually consistent, but accepted data should not be silently lost.

The ingestion path receives a stricter target because agent execution should not lose telemetry simply because an analytics page is degraded.

## 61.2 Latency

Initial targets:

p50 batch ingestion acknowledgement: < 50 ms excluding WAN latency;

p95 batch ingestion acknowledgement: < 150 ms under normal load;

p95 live event propagation after acceptance: < 1 second;

p95 run-detail query for a typical run (< 2,000 events): < 500 ms;

p95 dashboard aggregate query: < 1.5 seconds;

p95 policy decision when synchronous enforcement is enabled: < 75 ms within-region.

## 61.3 Durability

Accepted events must have a documented durability state. For MVP, 202 Accepted means the event batch has committed to PostgreSQL. In the Kafka architecture, acknowledgement may move to successful durable append to Kafka, after which downstream storage is asynchronous.

## 61.4 Scale targets by stage

### Stage A: portfolio/MVP

```text
projects:              < 100
active users:          < 1,000
runs/day:              < 100,000
events/day:            < 10 million
peak events/sec:       < 1,000
```

### Stage B: early product

```text
runs/day:              1-10 million
events/day:            100 million+
peak events/sec:       10,000+
```

### Stage C: platform

```text
multiple billions of events/month
independent processor fleets
columnar analytics
queue-backed ingestion
multi-region read paths
```

Architecture should grow stage-by-stage. Stage C machinery is not an MVP requirement.

# 62. Architectural Invariants

These invariants are more important than individual technology choices.

## INV-1: Canonical telemetry is immutable

Raw accepted events are immutable. Corrections are represented by new events or regenerated derived views, never silent mutation of history.

## INV-2: Derived state is rebuildable

Run summaries, cost totals, health scores, indexes, and dashboard aggregates must be reproducible from canonical events plus versioned metadata.

## INV-3: Every operation has tenant context

No repository or service method that accesses tenant-owned data should be callable without workspace/project context.

## INV-4: SDK failure must not crash the host agent

Telemetry is secondary to the user's application. Exporter errors are isolated, rate-limited in logs, and never allowed to propagate into the instrumented agent unless the user explicitly configures enforcement mode.

## INV-5: Sensitive payload capture is opt-in or configurable

Metadata needed for traces should be separable from potentially sensitive content.

## INV-6: Agent-specific concepts are first-class

Do not flatten everything into generic log strings. Tool calls, model calls, approvals, file modifications, retries, evaluations, and agent transitions need typed semantics.

## INV-7: Storage is behind contracts

Control-plane code does not issue arbitrary queries directly against event storage. Repository/service interfaces preserve the ability to move high-volume telemetry independently.

## INV-8: External semantic conventions are adapted, not copied into the domain blindly

OpenTelemetry alignment is valuable, but external schemas evolve. Mapping lives at the boundary.

# 63. Canonical Domain Model

The stable domain model is:

```text
Workspace
  └── Project
       ├── Agent Definition / Agent Version
       ├── Policy Set
       ├── Evaluation Definitions
       └── Run
            ├── Trace
            │    └── Span
            │         ├── Event
            │         └── Artifact reference
            ├── Evaluation results
            ├── Security findings
            └── Derived summary
```

## 63.1 Workspace

A workspace is the security and billing boundary. Workspace deletion, retention, export, access control, and quotas operate at this level.

## 63.2 Project

A project groups runs belonging to one product or logical agent system. API keys are scoped to projects by default. Cross-project keys are reserved for workspace administrators.

## 63.3 Agent definition and version

An agent is a logical identity such as coding-agent or reviewer. A version is a deployable behavior identity. Do not rely solely on Git SHA because the agent behavior may also depend on prompts, configuration, tool sets, and model choices.

Recommended version fingerprint:

```text
agent_version_id = hash(
  source_revision,
  prompt_bundle_version,
  tool_manifest_version,
  runtime_config_version
)
```

## 63.4 Run

A run is a user-meaningful unit of work. A trace is the technical causal graph. Most simple integrations use one root trace per run, but the domain keeps them conceptually distinct so a long-running business run can contain multiple technical traces later.

## 63.5 Span

A span has duration and parentage. Examples:

model inference

tool invocation

sub-agent execution

terminal command

database query

retrieval step

evaluation

## 63.6 Event

An event is an immutable point-in-time fact associated with a run and optionally a span. Events can describe state transitions, retries, approvals, file modifications, warnings, or structured output.

## 63.7 Artifact

Artifacts are potentially large or binary payloads. Event rows contain artifact references rather than embedding large data.

Examples:

complete terminal output

screenshot

patch file

prompt body

model response body

repository snapshot

uploaded dataset

# 64. Event Contract and Schema Governance

The event contract is the single most important technical asset in the project. UI components, processors, analytics, and storage can change. Once SDKs are deployed, event compatibility becomes expensive to break.

## 64.1 Envelope

Every event has a common envelope:

```text
{
  "schema_version": "1.0",
  "event_id": "01J...",
  "workspace_id": "ws_...",
  "project_id": "prj_...",
  "run_id": "run_...",
  "trace_id": "trc_...",
  "span_id": "spn_...",
  "parent_span_id": "spn_parent",
  "agent_id": "agt_...",
  "agent_version": "sha256:...",
  "event_type": "tool.call.completed",
  "occurred_at": "2026-10-06T20:13:22.029Z",
  "sequence": 42,
  "status": "success",
  "attributes": {},
  "payload_ref": null,
  "sdk": {
    "name": "agent-blackbox-python",
    "version": "0.1.0"
  }
}
```

## 64.2 ID generation

Prefer sortable globally unique identifiers such as UUIDv7 or ULID for externally generated IDs. Requirements:

clients can generate IDs offline;

collision probability is negligible;

IDs do not expose database sequence counts;

ordering by ID roughly corresponds to creation time when useful.

Never use an auto-increment database ID as the public event identity.

## 64.3 Event naming

Use dot-delimited names:

```text
run.started
run.completed
agent.spawned
llm.request.started
llm.request.completed
tool.call.started
tool.call.completed
file.modified
shell.command.completed
policy.action.blocked
approval.requested
```

Names are nouns plus lifecycle verbs, not prose.

## 64.4 Attributes versus payloads

attributes contain small searchable metadata. Large, sensitive, or unbounded values belong in a payload/artifact.

Good attributes:

```text
llm.provider
llm.model
llm.input_tokens
tool.name
tool.operation
file.path
shell.exit_code
```

Bad attributes:

```text
entire 50KB prompt
full terminal output
entire source file
```

This matters because searchable high-cardinality fields have storage and indexing cost.

## 64.5 Schema evolution

Rules:

Adding optional fields is backward compatible.

Existing field meaning never changes silently.

Renames require a deprecation window.

Removing a field requires a major schema version.

Consumers must ignore unknown optional fields.

Producers must declare schema version.

Storage preserves original version.

Normalizers may materialize a current internal representation but must keep raw input available during migration windows.

## 64.6 Schema registry

Store canonical schemas in a dedicated package:

```text
packages/event-schema/
  schemas/
    1.0/
      event.json
      llm.json
      tool.json
      file.json
```

CI should validate:

example payloads;

compatibility against previous minor versions;

generated SDK types;

API documentation.

# 65. Ordering, Clocks, and Causality

Distributed telemetry cannot assume perfect clock synchronization or arrival order.

## 65.1 Ordering fields

Each event contains:

occurred_at: client-observed wall-clock timestamp;

received_at: server timestamp;

sequence: monotonic within a run or local emitter stream;

trace/span parent relationships.

The UI uses a deterministic ordering function, for example:

```text
primary: logical sequence where available
secondary: occurred_at
tertiary: received_at
quaternary: event_id
```

## 65.2 Late events

A run can be marked complete and still receive late telemetry. Completion is therefore a lifecycle status, not a lock on the event stream.

Derived summaries must support recomputation or incremental correction.

## 65.3 Causal display

Chronological order and causal order are not identical. The timeline should offer both:

chronological list;

trace tree/waterfall.

A child span that overlaps its parent should appear nested even if timestamps are slightly skewed.

# 66. Idempotency and Delivery Semantics

Exactly-once end-to-end delivery is not required. It is expensive and unnecessary for telemetry if consumers are idempotent.

The contract is at-least-once submission with idempotent persistence.

## 66.1 Event deduplication

The database enforces uniqueness on (workspace_id, event_id).

Repeated batches return success for already-accepted events rather than creating duplicates.

## 66.2 Batch idempotency

Each batch can carry a batch_id. The server may store acknowledgement state to make retries efficient, but event IDs remain the source of truth.

## 66.3 Processor idempotency

Processors should be able to run the same event more than once. For example, cost aggregates use upserted per-event contributions rather than blind increment where possible.

# 67. SDK Architecture

The SDK consists of five layers:

```text
Instrumentation API
      ↓
Context Manager
      ↓
Event Builder / Sanitizer
      ↓
Buffer + Batch Queue
      ↓
Exporter
```

## 67.1 Instrumentation API

Public methods must be stable and minimal:

```text
tracer.start_run(...)
tracer.span(...)
tracer.event(...)
tracer.flush()
tracer.shutdown()
```

Framework-specific conveniences belong in integration packages, not the core SDK.

## 67.2 Context propagation

Use Python contextvars so async tasks maintain trace context without passing tracer objects everywhere.

Context contains:

```text
workspace/project identity
run_id
trace_id
current_span_id
agent_id
baggage/tags
```

When spawning threads/processes/sub-agents, adapters explicitly propagate required context.

## 67.3 Buffering

Default exporter behavior:

enqueue in memory;

flush on max batch size or max delay;

use a bounded queue;

drop or sample lower-priority telemetry under sustained pressure rather than unbounded memory growth;

expose dropped-event counters locally.

Suggested defaults:

```text
batch size:          100 events
flush interval:      250 ms
max queue:           10,000 events
HTTP timeout:        2 sec
retry attempts:      bounded with exponential backoff
```

All values are configurable.

## 67.4 Priority classes

Not all telemetry is equal.

Priority 0 - never intentionally drop:

```text
run lifecycle
errors
policy blocks
approval decisions
```

Priority 1:

```text
LLM/tool spans
file mutations
shell completion
```

Priority 2:

```text
verbose logs
debug token streams
high-frequency progress events
```

If local buffering saturates, lower priorities are sampled first.

## 67.5 SDK redaction pipeline

Sanitization must happen before serialization/export.

Pipeline:

```text
raw event
  ↓
field allow/deny rules
  ↓
secret pattern detector
  ↓
user callback redactor
  ↓
payload mode policy
  ↓
serialized event
```

The user can inspect or test redaction rules locally.

# 68. Python SDK Public API

A deliberately small v1 API:

```text
from blackbox import BlackBox

bb = BlackBox(
    api_key=os.environ["BLACKBOX_API_KEY"],
    project="coding-agent",
    endpoint="https://api.agentblackbox.dev",
)

with bb.run(name="Fix OAuth timeout", metadata={"issue": 1842}) as run:
    with run.span("plan", kind="agent"):
        plan = planner.create_plan()

    with run.span("github-search", kind="tool") as span:
        result = github.search_issues("oauth timeout")
        span.set_attribute("result_count", len(result))
```

## 68.1 Decorators

```text
@bb.observe(kind="tool")
def search_repo(query: str):
    ...
```

Decorators are convenience wrappers over spans and should not introduce separate semantics.

## 68.2 LLM instrumentation

Two modes:

### Explicit wrapper

```text
with run.llm_call(provider="openai", model="...") as llm:
    response = client.responses.create(...)
    llm.record_usage(...)
```

### Adapter/auto-instrumentation

Provider integrations patch or wrap supported client methods and generate the same canonical events.

The explicit API always remains available so unsupported providers can be traced.

# 69. TypeScript SDK Design

The TypeScript SDK should mirror concepts, not necessarily syntax.

```text
const bb = new BlackBox({ apiKey, project: "coding-agent" });

await bb.run("Fix OAuth timeout", async (run) => {
  await run.span("github-search", { kind: "tool" }, async (span) => {
    const result = await github.searchIssues("oauth timeout");
    span.setAttribute("result_count", result.length);
  });
});
```

Use AsyncLocalStorage for context propagation in Node.js.

The canonical schema package generates or exports shared TypeScript types so web, SDK, and backend contracts do not drift.

# 70. Framework Adapter Architecture

An adapter is a translation layer, not a privileged code path.

```text
Framework callback
      ↓
Adapter
      ↓
Core SDK API
      ↓
Canonical events
```

Every integration must pass a conformance suite that verifies it produces equivalent canonical events for equivalent operations.

Adapter package layout:

```text
integrations/
  langgraph/
  openai-agents/
  mcp/
  crewai/
  autogen/
```

Adapters should be separately versioned where framework compatibility makes that useful.

# 71. Ingestion API Low-Level Design

## 71.1 Endpoint

```text
POST /v1/events/batch
Authorization: Bearer <project-api-key>
Content-Encoding: gzip
Content-Type: application/json
```

Request:

```text
{
  "batch_id": "01J...",
  "sent_at": "2026-10-06T20:13:22Z",
  "events": [ ... ]
}
```

Response:

```text
{
  "batch_id": "01J...",
  "accepted": 100,
  "duplicates": 0,
  "rejected": 0,
  "server_time": "2026-10-06T20:13:22.100Z"
}
```

Partial rejection should include per-event machine-readable errors only when necessary. The normal path should remain compact.

## 71.2 Request path

```text
HTTP request
  ↓
TLS termination
  ↓
request size limit
  ↓
API-key lookup/cache
  ↓
tenant/project resolution
  ↓
rate limit
  ↓
schema validation
  ↓
redaction enforcement checks
  ↓
transactional insert / durable append
  ↓
live-stream publish trigger
  ↓
202 response
```

## 71.3 Rate limiting

Use token bucket semantics per project/API key. Rate limits should be expressed primarily in events/sec and bytes/sec rather than HTTP requests/sec because SDKs batch requests.

Return 429 with Retry-After. The SDK obeys backoff and preserves bounded buffering.

## 71.4 Payload limits

Recommended initial limits:

```text
single event serialized:     256 KB
batch uncompressed:            5 MB
artifact direct upload:       separate endpoint/presigned URL
```

Do not send multi-megabyte terminal logs through the event endpoint.

# 72. Background Processing Model

Derived work is asynchronous.

Initial implementation:

```text
event transaction
   ├── event row
   └── outbox/job row
          ↓
worker claims job
          ↓
processor
          ↓
derived records
```

This avoids the dual-write problem where an event commits but its processing job disappears.

## 72.1 Job claiming

PostgreSQL implementation can use row locking with FOR UPDATE SKIP LOCKED and leases.

Fields:

```text
job_id
job_type
workspace_id
payload
status
attempt_count
available_at
lease_owner
lease_expires_at
created_at
```

## 72.2 Processor categories

run summarizer

cost calculator

anomaly detector

search indexer

evaluation scheduler

alert evaluator

artifact analyzer

Each processor must be idempotent and retryable.

## 72.3 Dead-letter handling

After a bounded retry count, jobs move to dead_letter with the last error and stack trace. Operators can inspect and replay them after a fix.

# 73. PostgreSQL Physical Design

## 73.1 Transactional tables

Core tables:

```text
users
workspaces
workspace_members
projects
project_api_keys
agents
agent_versions
runs
spans
events
artifacts
evaluations
policies
approvals
security_findings
alerts
outbox_jobs
```

## 73.2 Index strategy

Do not index every event attribute. Start with access patterns:

```text
runs(workspace_id, project_id, started_at desc)
runs(workspace_id, status, started_at desc)
events(workspace_id, run_id, sequence)
events(workspace_id, run_id, occurred_at)
events(workspace_id, event_type, occurred_at desc)
spans(workspace_id, run_id, parent_span_id)
```

JSON attributes can receive targeted expression indexes only after query evidence exists.

## 73.3 Partitioning

Do not partition tiny tables. If PostgreSQL remains the raw event store beyond MVP, partition events by time, likely monthly, because retention deletion is then cheap. Avoid tenant-per-partition because high-cardinality partitioning is operationally expensive.

## 73.4 Row-level security

Application authorization remains mandatory. PostgreSQL Row-Level Security can be added as defense in depth for hosted multi-tenancy, with request-scoped tenant identity. It should not replace service-layer authorization tests.

## 73.5 Connection pooling

Use a production pooler or managed connection pooling. Short-lived serverless-style web instances must not create unbounded direct database connections.

# 74. ClickHouse Scale Design

ClickHouse is introduced for large telemetry/event analytics, not as the transactional source of truth.

## 74.1 Data model

A flattened event table favors analytical scans:

```text
workspace_id
project_id
run_id
trace_id
span_id
event_type
occurred_at
duration_ms
status
agent_id
model_provider
model_name
input_tokens
output_tokens
cost_usd
tool_name
error_type
attributes_json
```

Frequently filtered fields deserve typed columns. Arbitrary attributes remain semi-structured.

## 74.2 Ordering key

A reasonable initial order key:

```text
(workspace_id, project_id, toDate(occurred_at), run_id, occurred_at, event_id)
```

Exact design should follow observed queries.

## 74.3 Partitioning

Partition by coarse time period such as month, primarily to support retention and manageable data lifecycle. Do not partition by workspace because tenant cardinality can become very high and produce excessive small parts.

## 74.4 Materialized aggregates

Potential aggregates:

cost by workspace/project/model/hour;

run success by agent version/day;

latency histograms;

error counts by tool;

retry distributions.

Aggregates should accelerate dashboards while raw events remain queryable for investigations.

# 75. Object Storage and Artifact Lifecycle

Artifacts are stored outside relational tables.

## 75.1 Upload flow

```text
SDK requests artifact upload slot
        ↓
API validates tenant/quota
        ↓
returns presigned upload URL
        ↓
SDK uploads directly to object storage
        ↓
SDK/event records artifact metadata
```

For small local deployments, the SDK may post artifacts through the API, but hosted production should avoid proxying large payloads through application servers.

## 75.2 Content addressing

Store a cryptographic hash with each artifact. Benefits:

integrity verification;

optional deduplication;

stable identity for replay inputs.

## 75.3 Encryption

Use provider-managed encryption at rest at minimum. Future enterprise deployment may support customer-managed keys.

# 76. Live Streaming Architecture

For MVP, Server-Sent Events are preferred for run streaming because the dominant traffic is server-to-browser updates, automatic reconnection is simple, and the protocol works naturally over HTTP infrastructure.

WebSockets are introduced for features requiring client-to-server control messages, such as:

approve/deny action;

pause agent;

cancel run;

send operator note.

## 76.1 SSE protocol

Endpoint:

```text
GET /v1/runs/{run_id}/stream
Last-Event-ID: evt_...
```

Messages:

```text
id: evt_01J...
event: trace_event
data: {...}
```

On reconnect, the server queries events after the cursor and then transitions to live fanout.

## 76.2 Fanout at scale

Initial:

```text
API insert -> in-process/pub notification -> SSE clients
```

Scale:

```text
Kafka event
  ↓
stream fanout consumer
  ↓
Redis/NATS pubsub
  ↓
stateless stream gateways
  ↓
browsers
```

# 77. Query and Search Architecture

Search has two modes.

## 77.1 Structured filtering

Always supported:

```text
status=failed
model=...
cost_min=1.0
retry_min=3
event_type=shell.command.completed
file_path=src/auth/*
```

Structured search must be fast, explainable, and permission-safe.

## 77.2 Text search

MVP can use PostgreSQL full-text search for selected textual metadata. If search complexity grows, introduce a dedicated search index only when necessary.

Natural-language search should compile into structured filters and query plans rather than giving an LLM unrestricted raw database access.

# 78. Run Summary Materialization

Run summary fields are derived caches.

Example:

```text
{
  "duration_ms": 47012,
  "llm_calls": 4,
  "tool_calls": 12,
  "input_tokens": 18421,
  "output_tokens": 4102,
  "estimated_cost_usd": 0.18,
  "retry_count": 1,
  "error_count": 1,
  "files_modified": 2,
  "security_finding_count": 0
}
```

The summarizer updates incrementally as events arrive and performs a final reconciliation after run completion or after a quiet period for late events.

A summary_version identifies the summarization logic version so summaries can be rebuilt after semantic changes.

# 79. Cost Engine Detailed Design

Cost calculations must be reproducible.

## 79.1 Pricing table

```text
pricing_version
provider
model_pattern
valid_from
valid_to
input_token_price
output_token_price
cached_input_price
request_price
currency
source
```

## 79.2 Calculation record

Each model span can store:

```text
usage source: provider | estimated
pricing_version
calculated_input_cost
calculated_output_cost
calculated_total
```

## 79.3 Provider reconciliation

Future feature: ingest provider invoices/usage reports and compare estimated versus billed spend. The product must visually distinguish these values.

## 79.4 Cost budgets

Budgets can apply to:

run;

agent;

project;

workspace;

time window.

Enforcement mode must be explicit. A budget alert is not the same as a hard execution limit.

# 80. Failure Analysis Engine

Root-cause analysis begins deterministic and evidence-based.

## 80.1 Failure graph

For each failed run, identify:

terminal failure event;

ancestor spans;

preceding error events;

mutations between last known-good checkpoint and failure;

retries caused by the failure.

## 80.2 Deterministic attribution

Examples:

```text
shell command exit_code != 0 -> command failure
HTTP 500 -> external service failure
LLM provider timeout -> provider failure
policy deny -> security policy termination
```

## 80.3 Heuristic attribution

Later analyzers may identify likely causes such as:

code change immediately preceding tests;

repeated tool timeout;

context overflow;

invalid tool arguments.

Heuristic conclusions must be labeled as probable, not facts.

# 81. Loop and Anomaly Detection Engine

The detector framework operates over windows of events and historical baselines.

## 81.1 Initial deterministic detectors

### Repeated sequence detector

Normalize tool/event names and look for repeated n-grams.

### Identical request detector

Hash normalized tool arguments or prompts after redaction.

### Retry storm detector

Trigger when retries exceed absolute or time-window thresholds.

### Context growth detector

Alert when prompt token count grows monotonically beyond configured rate.

### Cost spike detector

Compare run cost to same agent/version historical percentiles.

## 81.2 Finding contract

```text
{
  "finding_type": "possible_loop",
  "severity": "warning",
  "confidence": 0.91,
  "start_event_id": "...",
  "end_event_id": "...",
  "evidence": {
    "pattern": ["search", "read", "reason"],
    "repetitions": 11
  },
  "detector_version": "loop-sequence@1.2.0"
}
```

Findings are versioned so historical results can be recalculated.

# 82. Coding-Agent Telemetry

Coding agents are the flagship demonstration and need specialized support.

## 82.1 File events

Capture:

```text
operation
path
size before/after
content hash before/after
diff artifact
language
```

By default, do not upload full source files when a diff or hash is sufficient.

## 82.2 Git events

Capture:

```text
repository identity
branch
base commit
head commit
changed files
commit hash
push target
PR number if applicable
```

## 82.3 Test events

Represent tests semantically instead of only terminal text:

```text
framework
suite
total
passed
failed
skipped
duration
failing test identifiers
```

This makes run comparison much stronger.

# 83. Terminal and Tool Safety Model

Commands and tools receive a capability classification.

Suggested command risk classes:

```text
R0 observation only
R1 local reversible mutation
R2 external reversible mutation
R3 privileged or destructive action
R4 catastrophic/high-impact action
```

Examples:

```text
cat file.txt                  R0
git checkout new-branch      R1
github.create_pr             R2
git push --force             R3
DROP DATABASE                R4
```

Risk is context-dependent, so classifiers can override defaults.

The initial product only observes classifications. Enforcement becomes an explicit integration mode later.

# 84. Multi-Agent Trace Semantics

A spawned agent is represented as a child span with its own agent_id and optionally its own agent version.

```text
Root run
  Planner span
    Research agent span
      Web search tool span
    Coding agent span
      LLM span
      File edit event
      Test span
    Reviewer agent span
```

The system must preserve:

parent that delegated work;

child agent identity;

delegation input;

child result;

timing overlap.

The UI should support grouping by agent lane in addition to nesting.

# 85. Evaluation System Low-Level Design

## 85.1 Evaluator contract

```text
class Evaluator(Protocol):
    name: str
    version: str

    async def evaluate(self, run: RunSnapshot) -> EvaluationResult:
        ...
```

Result:

```text
{
  "score": 0.92,
  "label": "pass",
  "explanation": "...",
  "metrics": {},
  "evidence_event_ids": []
}
```

## 85.2 Evaluator isolation

Evaluation failures do not change the original run status. They have independent states:

```text
pending
running
completed
failed
```

## 85.3 Deterministic evaluators

Examples:

test suite passed;

exact output schema;

expected file changed;

forbidden file untouched;

latency/cost thresholds.

## 85.4 Model judges

Model-based evaluation captures judge model, prompt template version, cost, and raw score metadata. Model judges are not treated as ground truth.

# 86. Experiment and Run Comparison Model

An experiment groups comparable runs.

```text
Experiment
  task dataset
  baseline configuration
  candidate configurations
  evaluator set
```

Comparison must separate variables. If model and prompt both change, the UI should show that this is not a controlled single-variable experiment.

Metrics:

```text
success rate
mean/median/p95 cost
mean/median/p95 latency
evaluation scores
retry rate
tool call count
failure category
```

# 87. Replay Architecture

Replay has three maturity levels.

## Level 1: visual replay

Purely replays recorded events in the UI. No execution risk.

## Level 2: mocked replay

The agent logic can be re-run while external model/tool calls return recorded responses. This is useful for testing state-machine logic.

Requirements:

deterministic lookup keys;

exact captured tool inputs/results;

versioned agent code environment;

clear indication that replay is simulated.

## Level 3: live branch replay

Re-executes external calls with changed variables.

Example:

```text
original -> fork at event 17 -> change model -> continue in sandbox
```

This requires:

sandboxing;

explicit user approval;

secrets management;

network controls;

cost guardrails;

immutable link back to parent run.

Level 3 is a later-stage feature and should not block the core product.

# 88. Security Policy Engine

The policy engine receives an ActionIntent before a sensitive action executes.

```text
{
  "workspace_id": "...",
  "project_id": "...",
  "run_id": "...",
  "agent_id": "...",
  "capability": "github.merge",
  "resource": "repo:org/project#pr-188",
  "risk_class": "R3",
  "arguments": {}
}
```

Result:

```text
{
  "decision": "require_approval",
  "policy_id": "pol_...",
  "reason": "Production merges require human approval"
}
```

## 88.1 Decision states

```text
ALLOW
DENY
REQUIRE_APPROVAL
ALLOW_WITH_CONSTRAINTS
```

## 88.2 Policy evaluation principles

deny takes precedence unless an explicit higher-priority exception exists;

policy order is deterministic;

every decision records policy/version;

policy evaluation has a strict latency budget;

default behavior on policy-service outage is configurable by capability, not globally.

High-risk actions should generally fail closed. Low-risk observation can fail open if the user chooses.

# 89. Approval Workflow

State machine:

```text
REQUESTED
  ├── APPROVED -> action resumes
  ├── DENIED   -> action receives policy error
  ├── EXPIRED  -> configured fallback
  └── CANCELLED
```

The approval record contains:

requester agent/run;

requested action;

policy that triggered approval;

approver identity;

decision timestamp;

optional comment;

expiration.

Approval tokens must be single-use and bound to the exact action intent so an approval for one command cannot be replayed for another.

# 90. Threat Model

The product observes potentially sensitive autonomous systems, so its threat model is serious even as a side project.

## 90.1 Assets

customer prompts/responses;

source code diffs;

terminal output;

API keys;

tool arguments/results;

policy configuration;

approval authority;

trace history;

user identity.

## 90.2 Trust boundaries

```text
Customer agent process
   | Internet boundary
Ingestion edge
   | Service boundary
Control plane
   | Data boundary
Databases/object storage
   | Human boundary
Dashboard users/approvers
```

## 90.3 Major threats

### Credential compromise

Mitigations:

API keys stored hashed where possible;

keys shown only at creation;

scoped credentials;

revocation;

rotation;

least privilege.

### Cross-tenant access

Mitigations:

workspace scoping in every repository method;

authorization middleware;

database RLS defense in depth;

automated tenant-isolation tests.

### Sensitive telemetry leakage

Mitigations:

client-side redaction;

configurable payload capture;

encryption;

strict access roles;

short retention for raw content.

### Stored prompt injection affecting analyst features

Telemetry content is untrusted data. Any AI summarizer analyzing a trace must treat captured prompts/tool output as data, not instructions. Use strict prompt boundaries and never give the summarizer implicit operational credentials.

### Malicious approval replay

Bind approval to exact action hash and mark consumed atomically.

### Denial of service through telemetry flood

Use size limits, per-project quotas, rate limiting, bounded queues, and tenant-aware workload isolation.

# 91. Authentication, Authorization, and RBAC

Dashboard users authenticate through a standard identity provider. Authorization is application-owned.

Roles:

```text
OWNER
ADMIN
DEVELOPER
VIEWER
SECURITY
BILLING
```

Example permissions:

```text
run.read
payload.read
artifact.read
api_key.create
policy.write
approval.decide
workspace.members.write
billing.read
retention.write
```

Avoid hard-coding role checks throughout the application. Services call a central authorization helper with actor, action, resource.

# 92. API Key Design

API keys use a prefix for lookup and a secret component shown once.

Example:

```text
abb_live_prj_abc123.<secret>
```

Database stores:

```text
key_id
prefix
secret_hash
project_id
scopes
created_by
created_at
last_used_at
expires_at
revoked_at
```

Recommended scopes:

```text
events:write
artifacts:write
runs:read
policy:check
```

SDK keys should not be able to administer a workspace.

# 93. Privacy, Redaction, and Data Classification

Data classification:

```text
Class 0: public metadata
Class 1: operational metadata
Class 2: customer confidential content
Class 3: credentials/secrets - should not be stored
```

Examples:

```text
model name                 Class 0/1
token count                Class 1
source diff                Class 2
prompt content             Class 2
API key                    Class 3
```

## 93.1 Redaction strategy

Use layered defenses:

SDK allow/deny configuration.

Secret detector before transmission.

Server-side secret detector as backup.

access controls in dashboard.

retention limits.

The best place to prevent leakage is the customer process before data leaves it.

# 94. Retention and Deletion

Retention is policy-driven by data type.

Default hosted example:

```text
run metadata:        365 days
structured events:    90 days
prompt/response:      30 days
terminal artifacts:   30 days
security audit:       365 days
```

Users may choose shorter retention.

Deletion must propagate to:

PostgreSQL;

analytical storage;

object storage;

search indexes;

caches;

derived aggregates where personally identifying content exists.

A deletion job records completion per backend and retries partial failures.

# 95. Frontend Information Architecture

Primary navigation:

```text
Workspace
├── Overview
├── Projects
│    └── Project
│         ├── Runs
│         ├── Agents
│         ├── Analytics
│         ├── Experiments
│         ├── Security
│         └── Settings
├── Alerts
├── Usage
└── Workspace Settings
```

## 95.1 Run page layout

Desktop layout:

```text
Header: run status / duration / cost / actions
------------------------------------------------
Left rail       Main trace area             Drawer
filters         timeline/waterfall          selected event
agents          live execution              details
------------------------------------------------
Bottom/secondary tabs: artifacts | evals | findings | metadata
```

Mobile is read-focused; advanced trace investigation is optimized for desktop.

# 96. Timeline UX Specification

Each event type receives a consistent visual grammar.

icon/category;

timestamp relative to run start;

primary label;

duration where applicable;

status;

compact key metadata;

expandable detail.

Events should collapse intelligently. For example, 100 streaming token events should not create 100 primary timeline rows.

## 96.1 Grouping

Possible groups:

```text
agent
span
retry attempt
tool
phase
```

## 96.2 Failure emphasis

The first causal error should be visually distinguishable from downstream errors. The UI should avoid making every subsequent failed action equally red.

# 97. Trace Waterfall UX

The waterfall is optimized for performance bottlenecks and parallelism.

Requirements:

zoom/pan time axis;

hierarchical span rows;

agent lanes;

color by span kind/status;

critical-path highlighting;

hover metadata;

synchronized selection with timeline.

For very large traces, render only visible rows and aggregate collapsed nodes. Avoid putting tens of thousands of DOM nodes on screen.

# 98. Run Comparison UX

Comparison is not just two columns. It must identify divergence.

Views:

summary metrics;

aligned trace steps;

event sequence differences;

file diff differences;

evaluation score differences;

cost breakdown differences.

The comparison algorithm should normalize obvious nondeterministic noise such as generated IDs and timestamps when aligning semantically equivalent actions.

# 99. Accessibility and UX Quality Bar

The dashboard should meet WCAG 2.1 AA-level practices where practical.

Requirements:

keyboard navigable event list;

visible focus state;

sufficient text contrast;

status communicated by text/icon, not color only;

screen-reader labels on charts and interactive controls;

reduced-motion support for live traces/replay;

tabular fallback for complex visualizations.

The product is an engineering tool; density is acceptable, illegibility is not.

# 100. Frontend Technical Architecture

Use Next.js App Router with TypeScript.

Separate server-fetched page data from highly interactive client-side trace viewers.

Suggested modules:

```text
app/
  (auth)/
  w/[workspace]/
    projects/[project]/
      runs/
      runs/[runId]/
      analytics/
      security/
components/
  trace/
  timeline/
  charts/
  diff/
lib/
  api/
  auth/
  schemas/
```

Use a query/cache library where it meaningfully improves client state, but do not duplicate server state in global stores without reason.

Live run state is updated incrementally from SSE, then reconciled against authoritative REST/HTTP query responses after reconnects.

PART

PRODUCTION ENGINEERING

APIs, deployments, migrations, CI/CD, capacity, failure handling, DR, operations, and developer experience.

# 101. Platform API Architecture

The browser and administrative clients consume a separate query/control API from the telemetry ingestion endpoint. Keeping these workloads conceptually separate prevents a slow analytics request from becoming part of the SDK critical path.

Representative routes:

```text
GET    /v1/workspaces/{workspace_id}/projects
POST   /v1/projects
GET    /v1/projects/{project_id}/runs
GET    /v1/runs/{run_id}
GET    /v1/runs/{run_id}/events
GET    /v1/runs/{run_id}/spans
GET    /v1/runs/{run_id}/artifacts
GET    /v1/runs/{run_id}/stream
POST   /v1/runs/{run_id}/evaluations
GET    /v1/projects/{project_id}/analytics
POST   /v1/projects/{project_id}/policies
POST   /v1/approvals/{approval_id}/decision
```

## 101.1 Pagination

Use cursor pagination for event/run lists. Offset pagination becomes expensive and unstable as live data is inserted.

Cursor should encode a stable sort tuple such as:

```text
(started_at, run_id)
```

or

```text
(sequence, event_id)
```

Cursors are opaque to clients.

## 101.2 Error contract

All APIs return a consistent machine-readable error object:

```text
{
  "error": {
    "code": "EVENT_SCHEMA_UNSUPPORTED",
    "message": "Schema version 2.0 is not accepted by this endpoint.",
    "request_id": "req_...",
    "details": {}
  }
}
```

Do not expose stack traces to clients.

## 101.3 Request IDs

Every request receives a request ID propagated through logs and traces. SDK ingestion responses return it so support/debugging can correlate client errors with server telemetry.

# 102. API Versioning and Compatibility

The product has three separately versioned contracts:

public HTTP API;

telemetry event schema;

SDK package API.

These should not share one version number.

HTTP breaking changes use path or negotiated major versions. Event schemas use explicit schema_version. SDKs follow semantic versioning and declare which event schema versions they emit.

Compatibility matrix documentation should look like:

```text
Python SDK 0.8.x -> event schema 1.2
Python SDK 0.9.x -> event schema 1.3
API v1          -> accepts event schema 1.0-1.3
```

A server should accept at least one prior supported schema generation during rolling upgrades.

# 103. Service Boundary Evolution

Start as a modular monolith, not a distributed monolith.

Logical modules:

```text
identity
tenancy
projects
runs
telemetry
analytics
evaluations
security
alerts
artifacts
```

Modules have explicit service/repository interfaces even while running in one process.

A module graduates into a separate service only when one of these pressures exists:

distinct scaling profile;

distinct failure isolation requirement;

independently deployable team ownership;

language/runtime advantage;

strong security isolation requirement.

Likely first splits:

ingestion;

asynchronous processors;

live streaming gateway;

analytics query service.

# 104. Deployment Environments

Maintain at least:

```text
local
preview/PR
staging
production
```

## 104.1 Local

Docker Compose:

```text
web
api
postgres
optional local object store
```

A single command should bootstrap the stack and seed demo data.

## 104.2 Preview

Frontend preview per pull request. Backend preview can be shared initially, but schema-changing work should have isolated ephemeral databases when affordable.

## 104.3 Staging

Production-like configuration with lower scale. All database migrations execute here before production.

## 104.4 Production

Managed services are preferred for the side-project-to-product path because operational effort should focus on Agent Black Box, not running databases by hand.

# 105. Reference Production Deployment

A cloud-neutral architecture:

```text
CDN / Edge
   ↓
Next.js web
   ↓
API load balancer
   ↓
FastAPI replicas
   ├── PostgreSQL
   ├── Object Storage
   ├── Cache/PubSub (when enabled)
   └── Event Bus (scale stage)
            ↓
       Processor fleet
            ↓
       ClickHouse
```

Possible AWS mapping at scale:

```text
CloudFront
ECS/EKS
RDS PostgreSQL
S3
ElastiCache
MSK or managed Kafka
managed ClickHouse/ClickHouse Cloud
```

Cloud provider choice should remain deployment configuration, not domain logic.

# 106. Docker and Container Standards

Every deployable service has:

deterministic Dockerfile;

non-root runtime user;

health endpoint;

readiness endpoint where appropriate;

explicit memory/CPU expectations;

pinned base image digest in production release pipeline when practical;

no development secrets baked into image layers.

Use multi-stage builds to keep runtime images small.

# 107. Kubernetes Scale Plan

Kubernetes is a scale-stage orchestration choice, not a resume checkbox.

When introduced, separate workloads:

```text
api deployment
worker deployment
stream-gateway deployment
ingestion deployment
scheduled maintenance jobs
```

Autoscaling signals:

```text
API: CPU + request concurrency
ingestion: requests/sec + CPU
workers: queue lag
stream gateways: active connections + memory
```

Do not autoscale workers only on CPU if the real saturation signal is queue lag.

Use PodDisruptionBudgets and graceful termination so batches are flushed before pod exit.

# 108. Configuration Management

Configuration hierarchy:

```text
code defaults
  < environment config
  < workspace/project settings where allowed
```

Secrets never live in source-controlled configuration.

Config categories:

service endpoints;

database connection;

object storage;

rate limits;

retention defaults;

feature flags;

provider pricing refresh settings;

logging level.

Validate configuration at startup and fail fast for missing required production settings.

# 109. Feature Flags

Feature flags are useful for risky or staged capabilities:

replay;

security enforcement;

new event schema parsing;

ClickHouse reads;

new analytics engine;

new trace viewer.

Flags are not permanent architecture. Every flag has an owner and removal condition.

Prefer server-side evaluation for permission/security-sensitive features.

# 110. Database Migration Strategy

Use a migration framework with linear, reviewed migrations.

Rules:

application deploys must tolerate the database state during rolling deployment;

prefer expand/migrate/contract for breaking schema changes;

never combine a destructive column drop with code that still reads it in the same deployment;

large backfills run as controlled jobs, not blocking migration transactions;

migrations are tested against a production-like dataset size in staging where relevant.

Example expand/contract:

```text
add new nullable column
→ deploy dual-write code
→ backfill
→ deploy read-new code
→ stop writing old column
→ later drop old column
```

# 111. CI Pipeline

Required pull-request checks:

```text
format
lint
type-check
unit tests
API schema compatibility
event schema compatibility
migration check
frontend build
SDK package build
integration tests
security dependency scan
```

Main-branch pipeline additionally builds signed/versioned artifacts and deploys staging.

Production release should require staging success and an explicit release action, even if the project is maintained by one person. This discipline prevents accidental production deployment from every commit.

# 112. CD and Release Strategy

Services use independent deployable versions even in a monorepo.

Recommended flow:

```text
merge main
  ↓
automatic staging deploy
  ↓
smoke + integration tests
  ↓
promote immutable image
  ↓
production rolling deploy
  ↓
post-deploy health validation
```

Avoid rebuilding source between staging and production; promote the tested artifact.

# 113. SDK Release Strategy

SDKs are more difficult to upgrade than servers because users control deployment timing.

Before 1.0, still preserve compatibility deliberately. Deprecations should emit developer warnings for at least one minor release when practical.

Each SDK release includes:

changelog;

supported Python/Node versions;

emitted event schema version;

migration notes;

known integration compatibility.

Never require server and SDK to be upgraded atomically.

# 114. Testing Pyramid

## 114.1 Unit tests

Focus on pure semantics:

event construction;

redaction;

pricing calculations;

policy matching;

run summarization;

retry/loop detectors;

authorization decisions.

## 114.2 Repository tests

Run against real PostgreSQL, not only mocks.

Verify:

tenant scoping;

unique event deduplication;

transaction behavior;

cursor pagination;

indexes/query plans for critical paths.

## 114.3 Contract tests

SDK event fixtures are validated against server schemas.

Every adapter gets golden canonical-event fixtures.

## 114.4 Integration tests

Exercise:

```text
SDK -> API -> DB -> worker -> query API
```

## 114.5 Browser tests

Critical flows:

sign in;

create project/API key;

open live run;

filter trace;

inspect event;

compare runs;

approve/deny action when enabled.

## 114.6 Load tests

Load tests use realistic event distributions, not identical tiny JSON messages. Include large tool payload references, bursty run starts, long-lived live streams, and a mix of write/read traffic.

# 115. Chaos and Failure Testing

At scale stage, deliberately test:

PostgreSQL unavailable;

object store unavailable;

event bus partition unavailable;

processor crash mid-job;

duplicate event delivery;

delayed event arrival;

stream gateway restart;

clock skew;

provider pricing service stale;

policy engine timeout.

For each fault, define expected behavior before running the experiment.

# 116. Observability of Agent Black Box

Dogfood the same principles.

## 116.1 Metrics

```text
http_requests_total
http_request_duration
telemetry_events_received_total
telemetry_events_rejected_total
telemetry_bytes_received
worker_jobs_pending
worker_job_lag_seconds
processor_failures_total
sse_connections
sse_delivery_lag
postgres_pool_wait_seconds
artifact_upload_failures
```

## 116.2 Logs

Structured JSON with:

```text
timestamp
level
service
request_id
workspace_id where safe
project_id where safe
operation
error_type
```

Never log full captured prompts or secrets by default.

## 116.3 Traces

Trace the ingestion request through persistence and processing. When event processing becomes asynchronous, use trace links where parent/child semantics do not fit.

# 117. SLOs, SLIs, and Error Budgets

SLIs:

successful ingestion rate;

ingestion acknowledgement latency;

accepted-event durability;

dashboard query success;

live event freshness;

policy-decision availability.

Example ingestion SLO:

99.95% of valid authenticated event batches are durably accepted during a rolling 30-day window.

The team should avoid shipping risky features when the error budget is exhausted until reliability is restored.

# 118. Backpressure Strategy

Backpressure exists at four layers:

```text
agent SDK buffer
HTTP edge
message/job queue
storage
```

The design must not allow congestion at the bottom to create unbounded memory at the top.

## 118.1 SDK

Bounded queue + priority dropping/sampling.

## 118.2 API

Rate limits and 429/503 with retry hints.

## 118.3 Queue

Monitor lag. Scale consumers before retention limits are threatened.

## 118.4 Storage

Batch writes; reduce unnecessary indexes; isolate analytics from transactions; spill high-volume events to columnar storage at scale.

# 119. Capacity Model

A simple capacity model should exist before optimization.

Assume:

```text
50 events/run
2 KB average structured event after compression-related effects ignored
100,000 runs/day
```

Then:

```text
5,000,000 events/day
~10 GB/day raw structured payload before indexes/replication
~300 GB/month raw
```

At 1 million runs/day this becomes an order of magnitude larger. Payload/artifact capture can dwarf event metadata, which is why large content requires separate storage and configurable retention.

Capacity planning should therefore track separately:

event rows;

compressed event bytes;

artifact bytes;

indexes;

replicas;

backup overhead.

# 120. Performance Engineering

Do not optimize from intuition alone.

Performance workflow:

```text
measure
identify dominant path
profile
change one variable
load test
compare
```

Likely early bottlenecks:

JSON serialization/deserialization;

database insert/index overhead;

N+1 queries on run pages;

rendering giant trace trees;

artifact payload transfer;

aggregate dashboard queries.

Frontend should virtualize very long timelines. Backend should expose compact event summaries separately from large payloads so opening a run does not download megabytes unnecessarily.

# 121. Caching Strategy

Cache only data with clear reuse and invalidation semantics.

Good candidates:

API-key lookup metadata;

workspace/project permissions with short TTL;

model pricing tables;

project configuration;

expensive dashboard aggregates.

Poor candidates:

live run event lists that are changing continuously;

security approval state where stale reads could be dangerous.

Cache invalidation should occur through version keys or explicit events where possible.

# 122. Disaster Recovery

Production recovery objectives should be documented even for an early product.

Initial targets:

```text
RPO: <= 15 minutes for control-plane metadata
RTO: <= 4 hours for full service restoration
```

These can improve with product maturity.

Required practices:

managed PostgreSQL point-in-time recovery;

object storage versioning where appropriate;

infrastructure definitions in code;

documented restore procedure;

periodic restore test.

Analytics derived data can often be rebuilt from durable canonical sources and may have weaker recovery requirements.

# 123. Backup and Restore Verification

A backup that has never been restored is an assumption.

Quarterly or release-stage restore drill:

restore database to isolated environment;

verify schema and row counts;

open representative runs;

verify artifact references;

run summary reconstruction;

measure restore duration;

document gaps.

# 124. Incident Response

Severity example:

```text
SEV-1: cross-tenant exposure, widespread ingestion loss, approval bypass
SEV-2: major outage/degraded ingestion, significant data delay
SEV-3: partial feature failure, isolated analytics problems
SEV-4: minor bug/no material production impact
```

For every incident:

assign incident lead;

stabilize first;

preserve evidence;

communicate status;

document timeline;

perform blameless root-cause review;

create owned corrective actions.

Security incidents follow a separate restricted communication path.

# 125. Operational Runbooks

Minimum runbooks:

## Ingestion rejection spike

Check:

```text
schema errors
API-key failures
payload size violations
database saturation
rate limiting
recent deploy
```

## Worker lag

Check:

```text
queue depth
oldest job age
processor error rate
DB connections
external dependency latency
```

## Database saturation

Check:

```text
connection pool
slow queries
lock waits
index bloat
write rate
analytics query load
```

## Live-stream lag

Check:

```text
fanout queue
active connections
gateway memory
client reconnect storm
pubsub health
```

# 126. Security Operations

Security-sensitive events themselves need operational monitoring:

unusual API-key usage;

repeated cross-tenant authorization failures;

policy bypass attempts;

high-rate secret detections;

repeated approval failures;

administrative role changes.

Audit records should be append-only from normal product APIs.

# 127. Dependency Management and Supply Chain

Use lockfiles and automated dependency update tooling. Security scans should flag known vulnerable packages, but updates are still tested before release.

For SDK packages, minimize dependencies because every dependency is inherited by customer applications.

Build provenance and package signing can be introduced before broad external distribution.

# 128. Repository and Ownership Structure

Recommended monorepo:

```text
agent-black-box/
├── apps/
│   ├── web/
│   └── api/
├── packages/
│   ├── event-schema/
│   ├── sdk-python/
│   ├── sdk-typescript/
│   ├── ui/
│   └── shared-types/
├── integrations/
├── processors/
├── examples/
├── tests/
│   ├── contract/
│   ├── integration/
│   └── e2e/
├── infrastructure/
├── docs/
│   ├── adr/
│   ├── runbooks/
│   └── architecture/
└── scripts/
```

Even as a solo project, ownership boundaries help future contributors and prevent accidental coupling.

# 129. Coding Standards

Principles:

explicit types at service boundaries;

small pure domain functions where possible;

no raw SQL scattered through controllers;

no business logic in React presentation components;

no framework-specific objects inside canonical domain models;

errors use typed categories;

log structured context, not concatenated strings;

every external call has timeout behavior;

all retry loops are bounded.

# 130. Error Taxonomy

Typed errors improve analytics and user messaging.

Top-level categories:

```text
AUTHENTICATION
AUTHORIZATION
VALIDATION
RATE_LIMIT
DEPENDENCY
TIMEOUT
CONFLICT
NOT_FOUND
POLICY
INTERNAL
```

Telemetry-specific subtypes:

```text
SCHEMA_UNSUPPORTED
EVENT_TOO_LARGE
RUN_NOT_FOUND
ARTIFACT_UPLOAD_FAILED
PROCESSOR_FAILED
```

Retryability should be explicit on errors rather than inferred from status text.

# 131. Logging Policy

Never rely on logs as the product telemetry model. Internal service logs exist to operate Agent Black Box itself.

Rules:

no secrets;

no raw prompt bodies by default;

include request ID;

include service/module;

use structured fields;

error logs contain typed error and stack trace server-side;

sampling allowed for repetitive successful events;

security/audit events are not sampled.

# 132. Documentation Strategy

Documentation categories:

```text
Quickstart
SDK reference
Framework integrations
Concepts: runs/traces/spans/events
Self-hosting
Security/privacy
API reference
Troubleshooting
Architecture/ADRs for contributors
```

The quickstart must reach a visible first trace quickly. A developer should not need to understand the entire architecture to instrument one agent.

# 133. Developer Experience Acceptance Criteria

A new user should be able to:

create project;

copy API key;

pip install the SDK;

add fewer than ~10 lines for basic tracing;

run an agent;

see the run appear live.

If this takes a long tutorial, integration friction is too high.

# 134. Local Development Workflow

Target commands:

```text
make bootstrap
make dev
make test
make lint
make e2e
make demo
```

make bootstrap should:

validate prerequisites;

create .env from safe template;

start dependencies;

run migrations;

seed local workspace/project;

print dashboard/API endpoints.

# 135. Demo Data and Fixtures

Provide deterministic trace fixtures for frontend development so the UI does not require live LLM API spending.

Fixture categories:

successful coding run;

failed run with retry;

multi-agent run;

expensive run;

loop detection;

security block;

approval pending;

10,000-event stress trace.

Golden fixtures are also used for visual regression testing.

# 136. Flagship Coding-Agent Demo

The public demo repository contains a realistic but bounded bug.

Recommended scenario:

```text
OAuth refresh/session expiration behavior is wrong.
```

Trace script:

agent receives issue;

reads relevant files;

searches repository/tests;

calls model;

edits implementation;

runs tests;

first attempt fails one test;

agent inspects failure;

makes second patch;

all tests pass;

Agent Black Box shows final metrics.

The failure is important. A demo where everything succeeds immediately does not showcase observability.

# 137. Product Analytics

Measure whether Agent Black Box itself is useful.

North-star-style product signals:

projects that send a second day of traces;

runs inspected after ingestion;

failed runs where users open event details;

comparisons created;

alerts configured;

time from SDK install to first trace.

Avoid vanity metrics such as total events alone.

# 138. Launch Quality Bar

Before calling the MVP complete:

instrumentation takes minutes, not hours;

live trace is stable through reconnect;

duplicate events do not appear;

sensitive fields can be disabled/redacted;

a failed coding-agent run can be diagnosed from the UI;

cost values are explainable;

project data is tenant-isolated;

SDK outage behavior does not break the demo agent;

README reproduces setup from a clean machine;

CI is green;

basic load test passes target stage.

PART

EXECUTION PLAN & REFERENCE

Milestones, ADRs, risks, definitions of done, build checklists, contracts, and reference schemas.

# 139. Milestone Plan

## Milestone 0 - foundation

Deliverables:

monorepo;

local Docker stack;

Postgres migrations;

canonical event schema package;

API skeleton;

web shell;

CI.

Definition of done: one manually posted event appears in a development run page.

## Milestone 1 - SDK and live timeline

Deliverables:

Python SDK;

run/span/event APIs;

ingestion batching;

run list/detail;

SSE streaming.

Definition of done: demo agent actions appear live.

## Milestone 2 - coding observability

Deliverables:

file events;

diffs;

shell/test events;

retry visualization;

artifact storage.

Definition of done: flagship bug-fix demo is compelling without narration.

## Milestone 3 - analytics

Deliverables:

cost engine;

latency/success metrics;

model/tool breakdowns;

dashboard filters.

## Milestone 4 - multi-agent and waterfall

Deliverables:

sub-agent semantics;

trace tree;

waterfall;

critical path.

## Milestone 5 - reliability intelligence

Deliverables:

loop detector;

retry analysis;

deterministic failure categories;

findings UI.

## Milestone 6 - evaluations/comparison

Deliverables:

evaluator framework;

comparison UI;

experiments.

## Milestone 7 - security observation

Deliverables:

action risk classes;

security findings;

secret access warnings.

## Milestone 8 - control

Deliverables:

policy API;

enforcement hooks;

approval workflow;

audit trail.

# 140. Prioritization Framework

Score proposed features on:

```text
User value       1-5
Demo value       1-5
Strategic fit    1-5
Engineering cost 1-5 (inverse)
Risk reduction   1-5
Foundation value 1-5
```

Prioritize features that strengthen the core loop:

```text
instrument -> observe -> diagnose -> improve
```

Do not let adjacent features turn the project into an unrelated agent platform.

# 141. Architecture Decision Records

Create ADRs for decisions that future contributors may reasonably question.

Initial ADR set:

```text
ADR-001 Canonical event schema owned by Agent Black Box
ADR-002 PostgreSQL as MVP system of record
ADR-003 FastAPI modular monolith for v1
ADR-004 SSE as default live-stream transport
ADR-005 Large payloads stored as artifacts
ADR-006 At-least-once ingestion with idempotent persistence
ADR-007 OpenTelemetry compatibility through adapters
ADR-008 Kafka deferred until independent-consumer/throughput trigger
ADR-009 ClickHouse deferred until analytical scale trigger
ADR-010 Client-side redaction before export
```

Each ADR contains context, decision, alternatives, consequences, and revisit conditions.

# 142. Major Alternatives Considered

## Go-first backend

Pros:

efficient concurrency;

low memory overhead;

strong single-binary deployment.

Cons:

slower initial integration with Python agent ecosystem;

second language likely still needed for SDK/examples;

optimization before evidence.

Decision: Python/FastAPI first; preserve protocol boundary for later Go ingestion.

## WebSockets everywhere

Pros: bidirectional, familiar for live apps.

Cons: added connection/state complexity for a mostly one-way stream.

Decision: SSE for observation; WebSocket/control endpoint only where bidirectional semantics justify it.

## Store every event directly in ClickHouse from day one

Pros: scalable analytics.

Cons: extra operational surface and weaker fit for transactional control-plane data.

Decision: PostgreSQL initially; separate analytics store at scale.

## Kafka from day one

Pros: replayable log and fanout.

Cons: operational complexity disproportionate to MVP traffic.

Decision: outbox/job design first; Kafka later behind same processing interfaces.

# 143. Risk Register

## Risk: project becomes too broad

Mitigation: MVP has exactly three headline capabilities: live trace, run timeline, cost/error/tool analytics.

## Risk: tracing overhead changes agent behavior

Mitigation: async batching, bounded buffers, performance benchmarks, metadata-only modes.

## Risk: sensitive data leaks into telemetry

Mitigation: SDK-side redaction, opt-in payloads, secret detection, short retention.

## Risk: event schema becomes impossible to evolve

Mitigation: versioned envelope, compatibility tests, adapters, raw version preservation.

## Risk: UI collapses under large traces

Mitigation: pagination, virtualization, payload lazy-loading, server-side grouping.

## Risk: overengineering prevents completion

Mitigation: scale technology has explicit adoption triggers and is prohibited before trigger unless a new requirement justifies it.

## Risk: cost estimates become wrong when providers change pricing

Mitigation: versioned pricing records and visible estimated-vs-billed distinction.

# 144. Definition of Done for the Whole Project

The project is considered complete at the initial portfolio/product milestone when all of the following are true:

### Product

clear onboarding and first trace;

live run viewer;

durable historical runs;

detailed event inspection;

cost/tool/error summaries;

coding-agent diff and terminal support;

filtering/search;

polished flagship demo.

### Engineering

canonical schema documented and versioned;

SDK bounded-buffer behavior tested;

duplicate ingestion safe;

tenant isolation tested;

database migrations reproducible;

CI/CD operational;

backup path documented;

service observability exists;

major ADRs written.

### Security/privacy

API keys scoped and revocable;

client-side redaction works;

raw content capture can be disabled;

secrets are not logged by backend;

role checks cover administrative operations.

### Documentation

README quickstart;

architecture overview;

SDK API reference;

event model reference;

troubleshooting guide;

local development guide.

### Portfolio

public repository is understandable;

architecture diagram exists;

demo video is < 60 seconds and understandable without audio;

screenshots show timeline, waterfall/trace detail, and analytics;

resume bullets use measured results rather than invented scale.

# 145. Build Checklist: Empty Repository to MVP

Use this sequence unless a blocking dependency requires deviation.

Initialize monorepo and formatting/linting.

Add Docker Compose with PostgreSQL.

Define canonical IDs and event schema.

Add first database migration.

Implement API health/config modules.

Implement project/API-key seed path.

Implement POST /v1/events/batch.

Add idempotent event insert.

Add run query endpoints.

Build web project/run pages from fixture data.

Connect run pages to real API.

Implement SSE event stream.

Build Python SDK core context and span API.

Implement batch exporter.

Implement retry/backoff/bounded queue.

Trace a simple local script.

Render live timeline.

Add LLM usage event helpers.

Add model pricing engine.

Add tool span helpers.

Add coding-agent file/shell adapters.

Add artifact storage abstraction.

Add code diff viewer.

Add retry/error summary.

Build flagship demo agent.

Add dashboard aggregates.

Add contract/e2e/load tests.

Harden auth/redaction/tenant isolation.

Write documentation.

Record public demo.

# 146. Post-MVP Expansion Checklist

After the MVP is stable, choose expansion based on real pain.

Possible sequence:

```text
multi-agent traces
→ waterfall
→ run comparison
→ deterministic evals
→ experiment runner
→ loop/retry intelligence
→ security findings
→ policy enforcement
→ approvals
→ replay
→ scale storage/event bus
```

Do not build all branches simultaneously.

# 147. Suggested First 20 GitHub Issues

Create monorepo and shared tooling.

Docker Compose PostgreSQL setup.

Canonical event JSON Schema v1.

SQL schema for workspace/project/run/span/event.

API key hashing and authentication middleware.

Batch ingestion endpoint.

Event idempotency test suite.

Run list endpoint with cursor pagination.

Run detail endpoint.

Next.js workspace/project shell.

Run list UI.

Timeline component using fixtures.

SSE run stream endpoint.

Timeline live merge/reconnect logic.

Python SDK client configuration.

Python run/span context manager.

SDK async batch exporter.

LLM usage/cost fields.

Demo agent fixture generator.

End-to-end smoke test.

Each issue should include acceptance tests, not only implementation tasks.

# 148. Interview and Design-Review Defense

If asked why the system is designed this way, the concise engineering narrative is:

The stable asset is the telemetry contract, not any individual database or framework. I keep the MVP operationally simple with FastAPI and PostgreSQL, but isolate event storage, artifact storage, processing, and analytics behind contracts. The SDK uses asynchronous batched at-least-once export with idempotent server persistence so telemetry does not block agents. As volume and independent consumers grow, the same event contract can be durably appended to Kafka, analyzed in ClickHouse, and streamed through dedicated gateways without changing how agents are instrumented.

That answer communicates tradeoffs, evolution, and restraint rather than technology collection.

# 149. Reference: SQL Schema Sketch

The exact migration files are implementation artifacts, but the logical schema begins as:

```text
CREATE TABLE workspaces (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE projects (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id),
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, slug)
);

CREATE TABLE runs (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id),
    project_id UUID NOT NULL REFERENCES projects(id),
    trace_id UUID NOT NULL,
    status TEXT NOT NULL,
    name TEXT,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    summary_version INTEGER NOT NULL DEFAULT 1,
    summary JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE events (
    event_id UUID NOT NULL,
    workspace_id UUID NOT NULL,
    project_id UUID NOT NULL,
    run_id UUID NOT NULL REFERENCES runs(id),
    trace_id UUID NOT NULL,
    span_id UUID,
    parent_span_id UUID,
    event_type TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    sequence BIGINT,
    status TEXT,
    attributes JSONB NOT NULL DEFAULT '{}'::jsonb,
    payload_ref TEXT,
    schema_version TEXT NOT NULL,
    PRIMARY KEY (workspace_id, event_id)
);
```

IDs may use UUIDv7 generated by application code rather than database random UUID functions.

# 150. Reference: Core Event Examples

## LLM completion

```text
{
  "event_type": "llm.request.completed",
  "status": "success",
  "attributes": {
    "llm.provider": "example-provider",
    "llm.model": "model-x",
    "llm.input_tokens": 8421,
    "llm.output_tokens": 1208,
    "llm.cached_input_tokens": 0,
    "llm.latency_ms": 2410,
    "cost.estimated_usd": 0.063
  }
}
```

## Tool completion

```text
{
  "event_type": "tool.call.completed",
  "status": "success",
  "attributes": {
    "tool.name": "github",
    "tool.operation": "search_issues",
    "tool.latency_ms": 1320,
    "tool.result_count": 8
  }
}
```

## Shell failure

```text
{
  "event_type": "shell.command.completed",
  "status": "error",
  "attributes": {
    "shell.command": "npm test",
    "shell.exit_code": 1,
    "shell.duration_ms": 8210
  },
  "payload_ref": "artifact://terminal/..."
}
```

# 151. Reference: Policy DSL Example

```text
version: 1
policies:
  - id: deny-env-read
    description: Do not allow agents to read dotenv secret files.
    when:
      capability: filesystem.read
      resource: "**/.env*"
    decision: deny

  - id: approve-production-merge
    when:
      capability: github.merge
      attributes:
        environment: production
    decision: require_approval
    approver_role: security
    expires_in: 15m

  - id: deny-destructive-db
    when:
      capability: database.execute
      risk_class: R4
    decision: deny
```

Policy syntax should remain intentionally small until actual use cases prove the need for a more expressive language.

# 152. Reference: Search Grammar

Initial structured query examples:

```text
status:failed
cost:>1.00
retries:>=3
model:"provider/model"
tool:github
file:"src/auth/**"
event:shell.command.completed
severity:error
security:true
started:>=2026-10-01
```

The parser converts tokens into a typed filter AST, which then compiles into storage-specific queries. This prevents the UI from constructing raw SQL-like strings.

# 153. Reference: Run State Machine

```text
            ┌─────────┐
            │ QUEUED  │
            └────┬────┘
                 │ start
                 ▼
            ┌─────────┐
            │ RUNNING │
            └─┬──┬──┬─┘
              │  │  │
   wait       │  │  │ success
              │  │  ▼
              │  │ SUCCESS
              │  │
              │  └──── failure -> FAILED
              │
              ▼
    WAITING / WAITING_FOR_APPROVAL
              │
              └──── resume -> RUNNING

RUNNING may also transition to CANCELLED, TIMED_OUT, or BLOCKED.
```

State transitions are validated server-side. Historical transition events remain in the event stream.

# 154. Reference: Approval State Machine

```text
REQUESTED
  ├── APPROVED
  ├── DENIED
  ├── EXPIRED
  └── CANCELLED
```

Only REQUESTED is mutable. Decision transitions are atomic and audited.

# 155. Reference: Failure Matrix

Failure

Agent impact

SDK behavior

Server behavior

User-visible behavior

Telemetry API unavailable

None by default

Buffer/retry within bounds

N/A

Trace may be delayed

SDK queue full

None by default

Drop/sample low-priority events

N/A

Data-loss indicator if detectable

Invalid event schema

None

Record exporter error, do not crash

Reject invalid event

Integration warning

PostgreSQL unavailable

Ingestion degraded

Retry/backoff

Return retryable error

Live trace delayed

Worker unavailable

None

N/A

Events persist, derived data delayed

Summary marked processing

Object store unavailable

Agent continues unless artifact is required

Retry artifact upload

Preserve metadata/error

Artifact unavailable warning

Policy service unavailable

Depends on configured capability

Enforcement adapter applies fallback

Emit decision outage event

Explicit fail-open/fail-closed message

# 156. Reference: Performance Budgets

Set explicit budgets so regressions are visible.

```text
SDK synchronous event creation:        < 1 ms typical
SDK application-thread blocking:       near-zero outside buffer enqueue
batch payload:                         <= 5 MB
run initial page payload:              <= 500 KB typical
trace event detail:                    lazy-loaded
frontend first useful render:          < 2 sec on normal broadband target
live event display after server accept:< 1 sec p95
```

Benchmarks are tracked over releases.

# 157. Reference: Data Ownership Matrix

Data

Source of truth

Derived copies

Workspace/project membership

PostgreSQL

auth cache

Run metadata

PostgreSQL

analytics store

Raw events MVP

PostgreSQL

summaries/search

Raw events scale

durable event log/ClickHouse per chosen architecture

summaries/search

Large artifacts

object storage

CDN/cache

Cost summary

derived

run summary/analytics

Evaluation result

PostgreSQL

analytics

Security finding

PostgreSQL/derived processor record

analytics/alerts

# 158. Reference: Service Sizing Heuristics

Before real measurements, use conservative initial resources and autoscaling limits rather than oversized fixed instances.

A small API instance can support the MVP. Processor concurrency scales separately. Database size is driven more by retained event volume than request count.

Always use load-test results from realistic payloads before claiming throughput numbers publicly.

# 159. Reference: Glossary

Agent: autonomous or semi-autonomous software actor that uses models/tools to complete tasks.

Run: user-meaningful execution instance.

Trace: causally connected execution graph.

Span: duration-bearing operation in a trace.

Event: immutable point-in-time telemetry record.

Artifact: larger payload referenced by telemetry.

Finding: derived observation such as loop, anomaly, or security warning.

Evaluation: scored judgment attached to a run.

Policy: rule deciding whether an intended action is allowed.

Approval: human decision authorizing or denying a specific intended action.

Canonical schema: Agent Black Box's stable internal/public telemetry vocabulary.

Control plane: APIs/data that manage users, projects, policies, and configuration.

Data plane / telemetry path: path that receives and processes agent events.

# 160. Reference: External Standards and Technology Notes

The design intentionally aligns with mature ecosystem concepts rather than inventing every primitive.

OpenTelemetry semantic conventions provide common naming and trace semantics. Agent Black Box should map to these conventions while keeping an adapter boundary because generative-AI conventions and other semantic groups may evolve over time.

PostgreSQL Row-Level Security can provide database-level tenant filtering as defense in depth, but application-level authorization remains required.

Kafka provides ordering within a partition and supports idempotent producer behavior; if Kafka is introduced, partition keys should preserve the ordering scope the product actually needs, such as a run or trace.

ClickHouse is appropriate for high-volume analytical event data, but partitioning should remain coarse; high-cardinality tenant partitioning creates operational problems and is not recommended.

Next.js App Router is the selected frontend architecture because it supports modern React application patterns while allowing interactive client components for trace viewers.

FastAPI is selected for the initial API because it supports async request handlers and typed Python APIs, matching the primary SDK ecosystem.

These notes are design inputs, not implementation lock-in. Internal contracts should outlive individual framework versions.

# 161. Final Principal-Engineer Guidance

The temptation with a project like Agent Black Box is to prove technical sophistication by adding infrastructure. Resist that. The project's sophistication should come from correct abstractions, explicit failure semantics, a strong telemetry contract, excellent developer experience, and a path to scale, not from the number of technologies in the README.

The non-negotiable core is:

```text
Agent execution
     ↓
low-overhead SDK
     ↓
versioned canonical telemetry
     ↓
durable idempotent ingestion
     ↓
rebuildable derived understanding
     ↓
fast human investigation UX
```

Everything else is an evolution around that spine.

A successful implementation should make the product feel simple even though the system underneath is capable of becoming sophisticated. The user should install an SDK, run an agent, and immediately understand behavior that was previously opaque. The engineering team should be able to add a new detector, framework integration, analytics backend, storage engine, or policy type without rewriting the core execution model.

That is the standard to hold for every new feature: Does it strengthen the ability to observe, understand, evaluate, or safely control autonomous software without weakening the core contracts? If yes, there is a place for it. If not, it is probably a different product.

PART

REFERENCES

Current official documentation used to validate selected ecosystem assumptions as of October 6, 2026.

# Technology References

OpenTelemetry Semantic Conventions 1.44.0: https://opentelemetry.io/docs/specs/semconv/

PostgreSQL Row Security Policies: https://www.postgresql.org/docs/current/ddl-rowsecurity.html

Apache Kafka Producer Configuration / Idempotence: https://kafka.apache.org/40/configuration/producer-configs/

ClickHouse engineering guidance on observability and partitioning: https://clickhouse.com/resources/engineering/observability

ClickHouse best-practice guidance: https://clickhouse.com/blog/10-best-practice-tips

Next.js Documentation / App Router: https://nextjs.org/docs

FastAPI Concurrency and async/await: https://fastapi.tiangolo.com/async/

Reference note: External framework versions will change. The architecture deliberately places stable internal contracts around these dependencies so future upgrades do not redefine the product domain model.
