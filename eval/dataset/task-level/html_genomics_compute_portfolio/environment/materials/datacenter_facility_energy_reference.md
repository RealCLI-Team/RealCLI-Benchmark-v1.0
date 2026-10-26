# Consortium Data-Center Facility — Energy & Sustainability Reference

**Document owner:** Consortium Facilities & Infrastructure Engineering
**Purpose:** authoritative facility-boundary energy factors to be applied to the IT electrical
load quoted in `genomics_vendor_hardware_quote.xlsx`. The vendor quote states the cluster
electrical load as **≈ 114.2 kW "before PUE" (UPS included; facility cooling excluded, so apply
PUE separately)** — this document supplies the PUE, tariff and grid-carbon factors to apply.

These are the committed planning factors for the consortium's co-located enterprise HPC room.
Use them as the supplied authoritative evidence for all facility-boundary energy and
sustainability calculations; they are not to be treated as free assumptions.

---

## 1. Metric definition and facility boundary (standard)

Power Usage Effectiveness (**PUE**) is defined by **ISO/IEC 30134-2:2016** (also published as the
European standard **EN 50600-4-2:2016**) as:

> **PUE = Total Facility Energy / IT Equipment Energy**

- *IT Equipment Energy* = energy consumed by compute, storage and network equipment (the 114.2 kW
  "before PUE" load in the vendor quote — UPS/PDU losses included, facility cooling excluded).
- *Total Facility Energy* = IT Equipment Energy **plus** the facility overhead: cooling (CRAC/CRAH,
  chillers), power-delivery losses beyond the UPS, lighting and building services.
- An ideal PUE is **1.0** (all energy reaches IT). Real facilities are always > 1.0.

Facility boundary used throughout this consortium: **room-level (data-center hall)**, IT load metered
at the rack PDUs, facility total metered at the room's main distribution board.

## 2. Industry PUE benchmarks (authoritative references)

Published, third-party-audited PUE figures used to place this facility in context:

| Facility class | Representative PUE | Source |
|---|---|---|
| Ideal (theoretical floor) | 1.00 | ISO/IEC 30134-2:2016 |
| Hyperscale, best-in-class (immersion / free-cooling) | 1.02 – 1.10 | Google fleet-wide 1.10 (2021); Facebook Prineville 1.078 |
| Hyperscale / large cloud (air + economizer) | 1.10 – 1.22 | Google 1.10; Microsoft 1.22 |
| Audited colocation | ~1.18 | Switch SUPERNAP 7 (third-party audited) |
| **Enterprise / on-prem HPC room (air-cooled, mixed-age)** | **1.4 – 1.6** | Uptime Institute annual survey — global enterprise average ≈ 1.5 |
| Legacy / poorly optimized | 1.8 – 2.0 | ISO/IEC 30134-2:2016 illustrative range |

## 3. Committed planning factors for this facility

The consortium HPC room is an **air-cooled enterprise facility** (not hyperscale). The committed
factors below are to be applied to the 114.2 kW IT load for all portfolio energy, cost and carbon
calculations:

| Factor | Committed value | Basis |
|---|---|---|
| **Facility PUE (annualized)** | **1.4** | Air-cooled enterprise HPC room; consistent with Uptime Institute enterprise average (~1.5) and better than legacy (1.8+) due to hot-aisle containment. ISO/IEC 30134-2 boundary. |
| **Electricity tariff (blended)** | **$0.10 / kWh** | Committed commercial/industrial contracted rate for the hosting site. |
| **Grid carbon intensity** | **0.386 kg CO₂e / kWh** | Regional grid average emission factor for the hosting region. |

### Worked facility-boundary example
- IT load (from vendor quote): **114.2 kW**
- Facility load incl. cooling = 114.2 kW × **PUE 1.4** = **159.9 kW**
- Annual energy at continuous run = 159.9 kW × 8,760 h ≈ **1,401 MWh/yr**
- Annual energy cost = 1,401 MWh × $0.10/kWh ≈ **$140,056/yr**
- Annual carbon = 1,401 MWh × 0.386 kg CO₂e/kWh ≈ **541 t CO₂e/yr**

## 4. Sensitivity guidance

PUE and tariff dominate opex uncertainty. Report a sensitivity band around the committed base case:
- **PUE:** 1.2 (efficient) → 1.4 (committed base) → 1.6 (conservative).
- **Tariff:** $0.06 → $0.10 (base) → $0.18 / kWh.
- **Carbon factor:** substitute the local grid factor if the hosting region differs from the 0.386
  kg CO₂e/kWh regional average.

## 5. Existing-fleet comparison boundary

When comparing the proposed new build against the **existing on-prem GPU fleet** (see
`genomics_workload_portfolio.db → current_infrastructure`), apply the **same facility PUE (1.4)** to
both so the comparison is like-for-like at the facility boundary. Existing-fleet IT load is computed
from published NVIDIA TDPs (H100 700 W, A100 400 W, L40S 350 W, V100 400 W per the NVIDIA
datasheets/product pages in this evidence set); cards without a published datasheet in the evidence
set (RTX 3090, A6000, RTX 2080 Ti) retain a flagged estimate.
