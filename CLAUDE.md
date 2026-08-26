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

## Conventions

- Python ≥3.10, dependency น้อยที่สุด — ทุกอย่างที่คุยกับ vendor อยู่หลัง optional extra
- exit-code ทุกค่ามีความหมายร่วม (ดู `src/baton/exits.py`) — เป็นสัญญากับผู้เรียกใช้ ห้ามเปลี่ยนลอยๆ
- ทุก path ที่แตะข้อมูลผู้เรียนจริงต้องเทสต์ผ่าน fake เท่านั้น (ดู `tests/` — FakeMessenger, FakeEncoder ฯลฯ)
- ภาษาไทยใน string/output เป็นเรื่องปกติ (RUF001/003 เปิด ignore ไว้แล้ว) — อย่า "แปล" ข้อความไทยเป็นอังกฤษ
- คำอ้างใน README/docs ต้องตรงกับพฤติกรรมจริง — ถ้าแก้พฤติกรรม ให้ตามแก้เอกสารด้วย
