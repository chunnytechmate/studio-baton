# Studio Baton

ชุดเครื่องมือ CLI สำหรับสตูดิโอสอนตัวต่อตัว: ผู้เรียน สรุปบทเรียน การส่งข้อความ
วิดีโอ และปฏิทิน — สร้างเป็น release สาธารณะบน GitHub แต่ผู้ใช้หลักคือสตูดิโอของเจ้าของเอง
ซึ่งมีข้อมูลผู้เรียนจริงอยู่เบื้องหลัง adapter ต่างๆ (LINE/Telegram/Notion/Supabase/Google)

## Commands

```bash
uv sync --extra dev          # ติดตั้ง/ซิงก์ toolchain ลง .venv
.venv/bin/ruff check .       # lint
.venv/bin/mypy               # types (config จำกัด scope ที่ src/baton)
.venv/bin/pytest             # ทดสอบ (~2 นาที)
```

ตัวรัน gates (`.claude/scripts/gates.sh`) จะถูกติดตามลง origin/main พร้อม slice F3 (#42)
ตามลำดับ bootstrap ใน contract — ก่อนหน้านั้นคำตัดสินของ factory คือ CI บน GitHub
(แปด check จาก app 15368) และ cold critic ตามที่ contract กำหนด

## Conventions

- Python ≥3.10, dependency น้อยที่สุด — ทุกอย่างที่คุยกับ vendor อยู่หลัง optional extra
- exit-code ทุกค่ามีความหมายร่วม (ดู `src/baton/exits.py`) — เป็นสัญญากับผู้เรียกใช้ ห้ามเปลี่ยนลอยๆ
- ทุก path ที่แตะข้อมูลผู้เรียนจริงต้องเทสต์ผ่าน fake เท่านั้น (ดู `tests/` — FakeMessenger, FakeEncoder ฯลฯ)
- ภาษาไทยใน string/output เป็นเรื่องปกติ (RUF001/003 เปิด ignore ไว้แล้ว) — อย่า "แปล" ข้อความไทยเป็นอังกฤษ
- คำอ้างใน README/docs ต้องตรงกับพฤติกรรมจริง — ถ้าแก้พฤติกรรม ให้ตามแก้เอกสารด้วย

## Factory rules

Read `docs/factory/CONTRACT.md`, then `docs/factory/CHARTER.md`, before acting. The
contract is the source of truth for queue semantics and non-negotiable rules.

1. **Never merge.** GitHub branch protection is the enforcement boundary.
2. **Never edit factory policy** (`docs/factory/CHARTER.md`, `docs/factory/CONTRACT.md`,
   `AGENTS.md`, `.factory/`, `.claude/`, `.agents/`, `.codex/`) unless the human explicitly
   asks in this session.
3. **Never modify an existing test in an unattended run.** An interactive change needs
   explicit human approval and a human read.
4. **Gates fail closed.** Quote the `FACTORY_GATES:` line verbatim; `RED`, `MISCONFIGURED`,
   and required skips all block progress. Until #42 lands, the oracle is GitHub CI.
5. **Verification uses a fresh context.** Delegate to an independent verifier or critic.
6. **Claim one live issue per run** via the deterministic remote-branch claim, then move
   the issue to `factory:in-progress`.

Stop and hand back to a human when: gates go red twice on one item; work reaches a
`LOAD_BEARING` path; the diff exceeds the charter limit; the item stays ambiguous after
one clarification; or the review queue is at its charter limit — the binding constraint is
how many decisions pend the owner's judgment, not how many agents can run.

State lives in files, not conversations: one immutable record under `docs/factory/runs/`
per run, GitHub labels for operational state. Commit messages and PR bodies are written
for a reader who was not in this session and cannot ask what you were thinking.
