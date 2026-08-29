# 🧪 Project Sandbox

![Python](https://img.shields.io/badge/Python-3.10+-blue?style=flat-square)
![Status](https://img.shields.io/badge/Status-Active-lightgrey?style=flat-square)
![Type](https://img.shields.io/badge/Type-Exploratory-green?style=flat-square)

A small space for building tools that solve everyday friction.

---

## 🌱 Why "Sandbox"?

In my previous school, “Sandbox” was a space where students could explore learning outside the syllabus —  no grades, no pressure, just curiosity.

This repository is inspired by that idea.

It’s where I park projects that:

- 🔄 Experiment with automation  
- 🛠 Solve real workflow annoyances  
- 🧠 Explore systems thinking  

I suppose Project Sandbox is a detour built with intention, shaped by every rabbit hole i have chased. 🤪

---

## 📦 Projects

### 📅 42 Logtime Planner
Plan the 20 hours a week required against a busy schedule — auto-filling them from the 42 intra API and live calendar data.

It became a scheduling friction point: tracking study hours across multiple calendars and keeping them synchronized. Missing the 20 hours because of poor mental sum on my part. These motivated me to have a planning tool that reads actual hours from the 42 API, queries busy time from Google Calendar and iCloud (CalDAV), and lets me drag planned blocks around in the browser to sync back to a dedicated iCloud calendar.

Focus areas:
- Real-time calendar synchronisation
- Smart block placement and conflict detection
- Session-level tracking from 42 API
- One-directional iCloud sync to prevent conflicts

The deeper challenges weren't about calendar integration at all: reading raw location sessions (since rollups handle in-progress sessions ambiguously), splitting sessions across midnight, and keeping iCloud sync one-directional-per-action so uncommitted work is never mistaken for deletion.

📂 [`logtime-planner/`](./logtime-planner)

---


### ✅ Check Namelist  
A CLI tool for reconciling messy attendance lists against an official namelist.

It began as a small frustration with repetitive manual checking, and slowly evolved into a lightweight reconciliation engine.

Focus areas:
- Identity normalisation  
- Deterministic matching  
- Interactive reconciliation  
- Persistent alias mapping  

📂 [`check-namelist/`](./check-namelist)

---

### 📅 Make Calendar *(akan datang)*  
Tool to generate `.ics` calendar files from structured input.

Built to reduce repetitive scheduling setup.

📂 `make-calendar/`