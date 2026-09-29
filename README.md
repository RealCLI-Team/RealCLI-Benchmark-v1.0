<div align="center">
<img src="assets/figures/realcli-logo.jpeg" alt="REALCLI logo" width="220"/>
<h1>REALCLI-Benchmark-v1.0</h1>
<p>Evaluating Human–Agent Collaboration in CLI Agents</p>
</div>

**Dataset Coming Soon.** We are currently preparing the release of the RealCLI dataset and will make it publicly available soon.

<p align="center">
<a href="https://huggingface.co/datasets/RealCLI-Team/RealCLI-Benchmark-v1.0">
  <img src="https://img.shields.io/badge/Hugging%20Face-RealCLI--Benchmark--v1.0-yellow" alt="Hugging Face dataset">
</a>
<a href="https://github.com/RealCLI-Team/RealCLI-Benchmark-v1.0">
  <img src="https://img.shields.io/badge/GitHub-RealCLI--Benchmark--v1.0-black" alt="GitHub repository">
</a>
</p>

## 🎯 Abstract

REALCLI is a paired benchmark for evaluating command-line agents on both task execution and human–agent collaboration. It targets realistic, long-horizon CLI workflows in which an agent manipulates files, programs, services, data, and other system resources while keeping a user informed, responsive, and in control.

Unlike evaluations that provide a complete specification and inspect only the final environment state, REALCLI includes a collaboration track in which requirements are released progressively and a scripted user may ask for clarification, revise constraints, or request authorization for high-impact actions. The same underlying tasks, environments, and outcome verifiers are used in both tracks, enabling a controlled comparison between autonomous execution and user collaboration.

## 📊 Benchmark Overview

| Property | REALCLI |
| --- | ---: |
| Tasks | 101 |
| Top-level categories | 10 |
| Subdomains | 52 |
| Collaboration dimensions | 5 |
| Scenario templates | 51 |
| Evaluated scenario instances | 606 |
| Checklist items | 1,658 |
| Evaluation tracks | `EXEC` and `COLLAB` |

The benchmark covers system operations, data analysis, software engineering, games, security, science and computing, office suites, frontend construction, models and algorithms, and vibe coding.

### Evaluation tracks

- **REALCLI-EXEC**: the complete task specification is available at the beginning, and the agent works autonomously through the CLI.
- **REALCLI-COLLAB**: a scripted user progressively releases information and introduces collaboration probes during execution.

The collaboration framework covers five dimensions: **Understandability**, **Steerability**, **Observation & Recovery**, **Trust Calibration**, and **Safety & Risk Awareness**.

## 🖼️ Figures

### Example COLLAB episode

Progressive specification release, interaction probes, agent actions, and separate task-outcome and collaboration checks are shown in one COLLAB episode.

![REALCLI COLLAB episode example](assets/figures/realcli-overview.png)

### Statistics of REALCLI

![REALCLI statistics](assets/figures/realcli-statistics-v1.png)

### Construction pipeline

The benchmark is built from real CLI logs, validated base instances, interaction scenarios, script checks, end-to-end pilots, and final curation.

![REALCLI construction pipeline](assets/figures/realcli-construction-pipeline.png)

## 📋 Directory Structure

Each benchmark instance is self-contained. The interaction layer is kept separate from the original task artifacts:

```text
<instance_id>/
├── instruction.md
├── task.toml
├── environment/
├── tests/
├── solution/
├── interaction/
│   ├── script.json
│   ├── oracle_contract.json
│   └── traps/
└── _authoring/
```

`interaction/script.json` contains the user persona, opening and closing messages, information slices, assigned scenarios, probes, evidence sources, hard checks, and binary checklist items.

## 🚀 Quick Start

The released benchmark dataset is available on Hugging Face:

[**RealCLI-Benchmark-v1.0**](https://huggingface.co/datasets/RealCLI-Team/RealCLI-Benchmark-v1.0)

The repository is currently being prepared as the public home for the benchmark. Task packages, evaluation runners, and detailed execution instructions will be added with the artifact release.

We will release the datasets and evaluation framework in the near future; release notes and usage instructions will be updated here.

## Citation

If you use REALCLI, please cite:

> **REALCLI: Toward a Unified Evaluation of Human–Agent Collaboration in CLI Agents.**

The archival paper link and complete BibTeX entry will be added with the benchmark release.

## License

The license and usage terms will be added with the public artifact release.
