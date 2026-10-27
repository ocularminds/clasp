# Autonomous phone order — 27 October 2026

Order `ord_6ae0fa7a4cbdcb1cd7c4` completed on the deployed autonomous flow with real WhatsApp messages,
a real challenge photo, seller email approval and automatic settlement. Payment and courier services
were simulated. No real funds moved.

The $120 Zoom H6 order finished **RESOLVED_CREDIT**: **$36 store credit** to the buyer and **$84 released**
to the seller, with no refund and no remaining held balance. AWS Step Functions reached **SUCCEEDED at
19:37:22 UTC**. The execution is named `ord_6ae0fa7a4cbdcb1cd7c4` under `clasp-dev-order` in `us-east-1`.
The final account read was at 19:38:47 UTC.

## What ran

| UTC | Observed event |
|---|---|
| 19:24:42 | The buyer's WhatsApp conversation created the order and hosted checkout. |
| 19:27:12 | The signed mock payment confirmation produced one $120 HOLD. |
| 19:27:14 | Automatic mock courier pickup advanced the workflow to DISPATCHED. |
| 19:27:15 | SMS was rejected with `ServiceQuotaExceededException` / `MONTHLY_SPEND_LIMIT_REACHED_FOR_TEXT`. The delivery challenge records `sent_via=whatsapp`; the owner confirmed handset receipt. Seller notices fell back to email. |
| 19:30:03 | The buyer's report of a battery-cover problem opened a Truce. The delivery code was not used. |
| 19:34:58 | A fresh photo challenge was verified: the code was visible and matched, confidence 0.75. Damage was not confirmed. No dispatch reference photo was available, so item matching could not be established. |
| 19:35:39 | A $24 refund was denied because this buyer had already received a refund within 30 days. |
| 19:36:19 | A $36 credit request escalated for inconclusive evidence. |
| 19:36:24 | Step Functions entered `AwaitSellerApproval` after `SendApprovalEmail`. |
| 19:37:19 | Seller approval completed the wait; the executor recorded $36 CREDIT. |
| 19:37:21 | `SettleRemainder` recorded $84 RELEASE. SES accepted both evidence-pack messages; both were later found in the inbox. |
| 19:37:22 | The workflow reached `Resolved` and SUCCEEDED. |

The configured store-credit option is **30%**, not 10%. The photo's code match established freshness;
it did not establish damage or that the item matched a dispatch photo. The policy correctly required
human approval before issuing this credit.

## Money and authority

| Ledger entry | Amount | Clasp decision |
|---|---:|---|
| `led_6ae0fb101531ededbd63` — HOLD | $120.00 | `dec_6ae0fb0d20bb8771a6fb` |
| `led_6ae0fd6f9aa03c4ced3a` — CREDIT | $36.00 | `dec_6ae0fd6b54b5117e2584` |
| `led_6ae0fd71ba3fbacf7b99` — RELEASE | $84.00 | `dec_6ae0fd70d94a7d075fb7` |

All five decision records use engine `decionis-gateway` and Clasp reference-policy version `2026.10.27-1`.
The gateway admits these `/decide` calls to Clasp's policy service; the service enforces the caps,
evidence, velocity and approval rules and signs the executor authorization.

| Proposed action | Clasp result | Reason | Decionis dossier |
|---|---|---|---|
| Hold $120 | allow | `in_policy` | `091c0a20-c6f4-4489-8703-b50c180650ba` |
| Refund $24 | deny | `velocity_exceeded` | `58f00ebb-6813-4b6c-beae-3299d72dabfa` |
| Credit $36 | escalate | `policy_drift`, inconclusive evidence | `a36052b5-5b9e-492e-8072-ac52944715bc` |
| Credit $36 | allow | `seller_approved` | `c5d01c34-def3-4eb2-9260-ff4639b30c4b` |
| Settle $84 | allow | `in_policy` | `bdf42015-880d-4493-9060-9044bd9ff70a` |

These dossier references were read from the account. Their signatures were not independently verified
in this check. The separate [gateway verification](gateway-verification-2026-10-27.md) verifies the
three signed artifacts for the earlier synthetic $1 hold and identifies the active `clasp` bundle.
No gateway policy or binding was changed during this phone run.

## Scope and remaining checks

- This run used an existing demo phone. Unknown-number WhatsApp enrolment remains untested on a new handset.
- The direct delivery-code release path and seller-to-supervisor escalation were not taken in this run.
- The seller approval email and both evidence-pack messages were found in the shared demo inbox after completion.
  The original hosted Approved page remained open and its decision/ledger IDs matched the account records.
- SMS production access and the $25 ceiling remain pending. Both available iPhones are unsupported for RCS.
- Four catalog items were restocked to five before the run; this order consumed one Zoom H6. Existing owners,
  phone roles, histories and refund allowances were preserved. Do not rerun the old seed configuration.
- The [published video](https://youtu.be/R1EKxswOsns) shows the separate 26 October $21.60 partial-refund take.
  Screens captured after this order completed must be labelled as such; they are not a recording of the
  pending approval or its button being pressed.

Account receipts and logs are retained in the ignored `demo/deployment-state/` directory. Codes,
signed approval URLs, task tokens, account identifiers and contact details are excluded from this record.
