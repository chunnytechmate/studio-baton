# Studio Baton

ชุดเครื่องมือ CLI สำหรับสตูดิโอสอนตัวต่อตัว: ผู้เรียน สรุปบทเรียน การส่งข้อความ
วิดีโอ และปฏิทิน ตัวโปรเจกต์เป็น release สาธารณะบน GitHub แต่ผู้ใช้หลักคือสตูดิโอ
ของเจ้าของเอง ซึ่งมีข้อมูลผู้เรียนจริงอยู่เบื้องหลัง adapter ต่างๆ
(LINE/Telegram/Notion/Supabase/Google)

## Commands

```bash
uv sync --extra dev          # ติดตั้ง/ซิงก์ toolchain ลง .venv
.venv/bin/ruff check .       # lint
.venv/bin/mypy               # types (config จำกัด scope ที่ src/baton)
.venv/bin/pytest             # ทดสอบ (~2 นาที)
```

## Conventions

- Python ≥3.10, dependency น้อยที่สุด ทุกอย่างที่คุยกับ vendor อยู่หลัง optional extra
- exit-code ทุกค่ามีความหมายร่วม (ดู `src/baton/exits.py`) ค่าพวกนี้เป็นสัญญากับผู้เรียกใช้ ห้ามเปลี่ยนลอยๆ
- ทุก path ที่แตะข้อมูลผู้เรียนจริงต้องเทสต์ผ่าน fake เท่านั้น (ดู `tests/`: FakeMessenger, FakeEncoder ฯลฯ)
- ภาษาไทยใน string/output เป็นเรื่องปกติ (RUF001/003 เปิด ignore ไว้แล้ว) อย่า "แปล" ข้อความไทยเป็นอังกฤษ
- คำที่ใช้ในงานเขียนทุกภาษาต้องผ่าน chunny-writing-style และคำที่ระบบใช้จับคู่กับเอกสารจริง
  (ชื่อวัน คำบอกเวลา หัวข้อ section prefix/closer ของข้อความ) เป็นข้อมูล ไม่ใช่งานเขียน ห้ามขัดถ้อยคำ
- คำอ้างใน README/docs ต้องตรงกับพฤติกรรมจริง ถ้าแก้พฤติกรรม ให้ตามแก้เอกสารด้วย

## Real-media test assets (นอก repo)

สื่อจริงสำหรับเทสแบบ live (คลิป Drive, ไฟล์รวม, job ค้างกลางทาง, สรุป) อยู่ที่
`tests/fixtures-staging-videos` เป็น symlink ไปยังชุดข้อมูลใต้ mount ของ OpenClaw
(container agent เข้าได้ที่ `/home/node/.openclaw/test-assets/`) ทั้งชุดถูก
gitignore ไว้ **ห้าม commit ห้าม rename** เพราะชื่อโฟลเดอร์และชื่อไฟล์คือชื่อที่
pipeline จริงจับคู่ ถ้าเทสต้อง mutate ให้ `cp -a` ไป throwaway profile ก่อน
รายละเอียดอยู่ใน `README.md` และ `MANIFEST.md` ของชุดนั้น
