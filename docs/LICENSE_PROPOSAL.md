# License Choice Proposal: Apache-2.0 vs. MIT

This document evaluates the licensing strategy for the open-source release of Sign-Flow and provides a formal recommendation for project leadership.

---

## 1. Executive Summary

Sign-Flow combines client-side browser vision, a Python-based background daemon, and low-level Linux X11 desktop integration. To ensure clean adoption, frictionless commercial deployment, and protection against intellectual property risks, the project must adopt a permissive open-source license.

**Recommendation**: Adopt the **Apache License, Version 2.0 (Apache-2.0)** for all first-party Sign-Flow code, with **MIT** as an acceptable alternative if absolute minimalism is preferred.

---

## 2. Comparative Analysis

| Dimension | Apache License 2.0 (Recommended) | MIT License |
|---|---|---|
| **Patent Grant** | **Explicit grant (Section 3)**: Provides express patent license from contributors to users, terminating if a user sues over patent infringement. | **Silent**: Contains no explicit patent language; courts infer implied licenses, creating legal ambiguity. |
| **Trademark Rights** | **Express reservation (Section 6)**: Clearly prohibits downstream forks from using the project's name, logos, or marks without permission. | **Silent**: Does not address trademarks, leaving mark protection reliant solely on common law. |
| **MediaPipe Compatibility** | **1:1 Alignment**: Upstream MediaPipe Tasks Vision (`@mediapipe/tasks-vision`) is licensed under Apache-2.0. | Compatible, but introduces dual-licensing complexity across vendored files. |
| **Attribution & Notices** | Requires preservation of copyright notices, license copy, and a `NOTICE` file for downstream redistributions. | Requires preservation of copyright notice and disclaimer only. |
| **Commercial Adoption** | Universal in enterprise infrastructure (Kubernetes, TensorFlow, Apache projects). | Universal in web and JavaScript ecosystems. |
| **Text Length** | Comprehensive legal instrument (~175 lines). | Minimalist template (~20 lines). |

---

## 3. Rationale for Recommending Apache-2.0

### 3.1 Protection via Explicit Patent Grant
Sign-Flow implements real-time hand gesture tracking, dynamic time warping trajectory matching, and automated window/keystroke injection. These algorithmic areas frequently encounter patent assertion claims in commercial enterprise software.
- Section 3 of Apache-2.0 grants recipients a perpetual, worldwide, non-exclusive, no-charge patent license for any patent claims licensable by contributors that are necessarily infringed by their contributions.
- Furthermore, it features a defensive termination clause: if a party institutes patent litigation alleging that the software infringes a patent, their patent license granted under Apache-2.0 terminates immediately.
- This shields both downstream users and original contributors from predatory intellectual property claims.

### 3.2 Upstream Alignment with Google MediaPipe
The core hand pose detection engine embedded in `web/vendor/mediapipe/` is licensed by Google under **Apache-2.0**.
- Releasing Sign-Flow under Apache-2.0 establishes full licensing parity across the runtime vision components and first-party orchestration code.
- Redistribution and packaging guidelines remain consistent throughout the entire repository.

### 3.3 Trademark Protection for the Sign-Flow Brand
Sign-Flow features distinct brand assets, including the lotus and blob design language.
- Section 6 of Apache-2.0 explicitly clarifies that the license does not grant trademark rights to use the trade names, trademarks, service marks, or product names of the licensor.
- This guarantees that third-party distributors may fork and redistribute the open-source code while preserving exclusive rights to the Sign-Flow brand identity.

---

## 4. Alternative: When to Select MIT

If project leadership prioritizes maximum simplicity and frictionless adoption in the JavaScript and Linux utility communities, the **MIT License** remains an established, fully compatible alternative:
- The existing repository root currently carries an MIT License header (`LICENSE`).
- Downstream developers can integrate Sign-Flow components into proprietary or permissively licensed applications with virtually zero friction.
- However, MIT does not include explicit patent licenses or trademark reservations.

---

## 5. Decision Checklist

- [x] **Compatibility with Dependencies**: Both Apache-2.0 and MIT are 100% compatible with Project Nayuki's QR generator (MIT) and MediaPipe (Apache-2.0).
- [x] **Commercial Usability**: Neither license imposes copyleft (GPL-style) requirements on downstream consumers.
- [x] **Notice Requirement**: Both licenses require maintaining attribution in `THIRD_PARTY_NOTICES`.

**Conclusion**: Select **Apache-2.0** for commercial robustness and patent defense, or retain **MIT** if developer minimalism is the governing priority.
