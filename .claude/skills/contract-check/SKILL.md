---
name: contract-check
description: Check this area's code against contracts/ before opening a PR.
---
Compare the code in the folder I am working in with contracts/api.md, contracts/flags.md
and contracts/dots.md. List every mismatch in names, types, units, endpoint paths and the
static file shapes under web/public/data/. Check that time is MJD-AVG, the band comes from
energy_bandpassname, and visits follow the visit rule. Fix the mismatches inside this area.
Do not edit contracts/. If the contract itself looks wrong, write a short proposal for P1.
Then run this area's tests and report the result.
Use the synthetic examples in fixtures/api/ as the reference shapes.
