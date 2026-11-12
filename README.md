<div align="center">

# ◈ NEXUS · Autonomous Dashboard Agent
### Real-Time Autonomous Analytics & Intelligent Operations Command Center

[![Python](https://img.shields.io/badge/Python-3.9%2B-blue?logo=python&logoColor=white)](https://python.org)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.32%2B-FF4B4B?logo=streamlit&logoColor=white)](https://streamlit.io)
[![Plotly](https://img.shields.io/badge/Plotly-5.18%2B-3F4F75?logo=plotly&logoColor=white)](https://plotly.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![GitHub](https://img.shields.io/badge/Author-Dheeraj%20Muley-black?logo=github)](https://github.com/dheeraj815)

**NEXUS** is an autonomous intelligence dashboard that monitors system metrics, schedules background agent operations, generates automated alerts, and visualizes complex telemetry in real time.

</div>

---

## 🚀 Key Features

- **Autonomous Agent Monitoring**: Tracks agent status, background tasks, error rates, and operational latency.
- **Background Scheduling**: Integrated `apscheduler` triggers interval tasks and periodic system health checks.
- **Interactive Telemetry**: Plotly subplots, high-frequency stream charts, and anomaly heatmaps.
- **Persistent Data Store**: SQLite backend recording event logs, metric snapshots, and historical alerts.
- **Executive UI Theme**: Streamlined dark-mode command center optimized for real-time operations.

---

## 🛠 Tech Stack

- **Frontend**: Streamlit
- **Visualization**: Plotly Graph Objects & Express
- **Task Scheduling**: APScheduler (BackgroundScheduler)
- **Data Engine**: Pandas, NumPy, SQLite3
- **Language**: Python 3.9+

---

## ⚡ Quick Start

```bash
# 1. Clone repository
git clone https://github.com/dheeraj815/Autonomous-Dashboard-Agent.git
cd Autonomous-Dashboard-Agent

# 2. Virtual environment setup
python -m venv venv
venv\Scripts\activate     # Windows
# source venv/bin/activate  # macOS / Linux

# 3. Install requirements
pip install -r requirements.txt

# 4. Launch dashboard
streamlit run app.py
```

---

## 📂 Project Structure

```
Autonomous-Dashboard-Agent/
├── app.py              # Main dashboard command center
├── requirements.txt    # Dependencies
├── .gitignore          # Ignores databases, video demos, and log streams
├── LICENSE             # MIT License
└── README.md           # Documentation
```

---

## 👤 Author

Developed by **Dheeraj Muley**  
GitHub: [@dheeraj815](https://github.com/dheeraj815)
