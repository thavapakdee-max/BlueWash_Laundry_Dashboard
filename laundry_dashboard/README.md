# BlueWash Laundry Dashboard (Prototype)

ระบบต้นแบบจัดการร้านซักรีดด้วย Flask + SQLite + HTML/CSS/JavaScript โทนฟ้า-ขาว

## ฟังก์ชันที่มี
- Dashboard สรุปจำนวนออเดอร์ งานระหว่างดำเนินการ งานพร้อมรับ และยอดชำระที่ยืนยันแล้ว
- จัดการออเดอร์: เพิ่ม ดูรายละเอียด ค้นหา กรอง แก้ไขน้ำหนัก/ราคา/สถานะ และลบ
- แยกสถานะงานซักรีดออกจากสถานะชำระเงิน
- แนบรูปถุงผ้าและสลิป (ยังไม่มีการตรวจสอบสลิปอัตโนมัติ)
- จัดการตัวเลือกน้ำยาซักผ้า/น้ำยาปรับผ้านุ่ม เปิด-ปิดตัวเลือกได้
- API เริ่มต้น: `GET /api/health`, `GET /api/orders`, `POST /api/orders`, `GET /api/orders/<id>`
- มีข้อมูลตัวอย่างเริ่มต้น 4 ออเดอร์สำหรับทดสอบหน้าจอ

## วิธีติดตั้ง (Windows)
1. ติดตั้ง Python 3.10 หรือใหม่กว่า
2. แตกไฟล์ ZIP
3. เปิด Terminal / Command Prompt ในโฟลเดอร์โปรเจกต์
4. สร้าง virtual environment (แนะนำ):
   ```bat
   py -m venv .venv
   .venv\Scripts\activate
   ```
5. ติดตั้งแพ็กเกจ:
   ```bat
   pip install -r requirements.txt
   ```
6. เริ่มระบบ:
   ```bat
   python app.py
   ```
7. เปิดเบราว์เซอร์ที่ `http://127.0.0.1:5000`

ฐานข้อมูล `laundry.db` จะถูกสร้างอัตโนมัติครั้งแรก และข้อมูลจะอยู่ในไฟล์นี้

## API ตัวอย่าง

ตรวจสุขภาพระบบ:
```http
GET /api/health
```

สร้างออเดอร์จากระบบ LINE ที่จะเชื่อมภายหลัง:
```http
POST /api/orders
Content-Type: application/json

{
  "customer_name": "ลูกค้าทดสอบ",
  "line_user_id": "Uxxxxxxxx",
  "service": "wash_dry",
  "detergent": "สูตรมาตรฐาน",
  "softener": "กลิ่นดอกไม้"
}
```

รหัสบริการที่รองรับ:
- `wash` = ซักอย่างเดียว
- `wash_dry` = ซักและอบแห้ง
- `wash_iron` = ซักและรีด

## ขอบเขตและข้อควรทราบ
- การเชื่อม LINE จริงต้องตั้ง LINE Login/LIFF ID และ public HTTPS URL ก่อน
- UI แสดงข้อมูลตัวอย่างสำหรับทดลอง ไม่ใช่ข้อมูลธุรกรรมจริง
- หน้า Dashboard ต้องล็อกอินแอดมินก่อน แต่ก่อนเปิดใช้งานสาธารณะควรเปลี่ยนรหัสผ่านเริ่มต้น, เพิ่ม CSRF protection, ใช้ WSGI server และกำหนด HTTPS/สิทธิ์เข้าถึงไฟล์ให้เหมาะสม
- ในต้นแบบนี้พนักงานเป็นผู้ยืนยันการชำระเงินเอง การแนบสลิปไม่เปลี่ยนสถานะเป็นชำระแล้วโดยอัตโนมัติ
- ราคาในข้อมูลตัวอย่างมีไว้ทดสอบหน้าจอเท่านั้น ไม่ใช่ราคาที่ร้านกำหนด

## LINE Official Account: ซักทีซะบ้อ

LINE Rich Menu คงปุ่มเริ่มใช้บริการ/ตรวจสอบสถานะ/ชำระเงิน/ติดต่อร้านไว้ในแชต โดยปุ่มเริ่มใช้บริการเปิดหน้า LIFF ส่วนคำสั่งอื่นตอบกลับในแชต Webhook `/webhook/line` ตรวจ `X-Line-Signature`, กัน event ซ้ำ และใช้ส่งข้อความตอบกลับ/แจ้งสถานะ ส่วน `/api/liff/orders` ตรวจ LINE ID token กับ LINE ก่อนสร้างออเดอร์ในตาราง `orders` เดิม รูปตะกร้าหรือถุงผ้าถูกเก็บใน `uploads` และพนักงานยังจัดการออเดอร์จาก Dashboard เดิม

### ตั้งค่า LINE Developers

1. ใช้ Provider เดียวกันกับ Messaging API channel ที่เชื่อม OA และสร้าง LINE Login channel; LIFF กับ Messaging API ต้องอยู่ใต้ Provider เดียวกันเพื่อให้ user ID เชื่อมกันได้
2. ใน LINE Developers → LINE Login channel → LIFF เพิ่ม LIFF app ด้วย Endpoint URL `https://<โดเมน>/liff/order` และเปิด scopes `openid` กับ `profile`; บันทึก LIFF ID เป็น `LIFF_ID` และ LINE Login channel ID เป็น `LINE_LOGIN_CHANNEL_ID`
3. ตั้ง `LINE_CHANNEL_SECRET` กับ `LINE_CHANNEL_ACCESS_TOKEN` จาก Messaging API channel ไว้ใน `.env` หรือ environment variables; ห้าม commit credentials จริง
4. ตั้ง `SECRET_KEY` เป็นค่าสุ่มยาว และกำหนด `PROMPTPAY_INSTRUCTIONS` กับ `SHOP_CONTACT_TEXT` ตามข้อมูลจริงของร้าน
5. Deploy Flask หรือเปิด HTTPS tunnel ให้เข้าถึงได้จากอินเทอร์เน็ต แล้วตั้ง Webhook URL `https://<โดเมน>/webhook/line` ใน Messaging API channel เปิด Use webhook และ Verify
6. ตั้ง Rich Menu ใน LINE Official Account Manager ให้ปุ่มเริ่มใช้บริการเปิด `https://liff.line.me/<LIFF_ID>` และปุ่มอื่นส่งข้อความ `ตรวจสอบสถานะ`, `ชำระเงิน`, `ติดต่อร้าน` ตามลำดับ จากนั้นเพิ่ม OA เป็นเพื่อนและทดสอบการสร้างออเดอร์/แจ้งราคา/สถานะ/ส่งสลิป

บัญชีผู้ดูแลระบบเริ่มต้นของโปรเจกต์นี้คือ `admin` / `123` หากตั้ง `ADMIN_USERNAME` หรือ `ADMIN_PASSWORD` ใน environment ไว้ ค่าดังกล่าวจะใช้แทนค่าเริ่มต้นนั้น

ตั้งค่า environment variables ที่ต้องใช้และรันใน PowerShell (ตัวแปรมีผลเฉพาะหน้าต่าง Terminal นี้):

```powershell
$env:SECRET_KEY = "<random-secret>"
$env:ADMIN_USERNAME = "admin"
$env:ADMIN_PASSWORD = "123"
$env:COOKIE_SECURE = "0"
$env:LINE_CHANNEL_SECRET = "<channel-secret>"
$env:LINE_CHANNEL_ACCESS_TOKEN = "<channel-access-token>"
$env:LINE_LOGIN_CHANNEL_ID = "<line-login-channel-id>"
$env:LIFF_ID = "<liff-id>"
$env:PROMPTPAY_INSTRUCTIONS = "โอนเข้าพร้อมเพย์ <ข้อมูลร้าน>"
python app.py
```

หน้า Dashboard และ API สำหรับจัดการออเดอร์ต้องเข้าสู่ระบบก่อน ส่วน `/webhook/line` เปิดให้ LINE เรียกได้โดยตรวจ signature ตามปกติ ตั้ง `COOKIE_SECURE=1` เมื่อให้บริการผ่าน HTTPS

แอปโหลดค่าจากไฟล์ `.env` ในโฟลเดอร์นี้เมื่อเริ่มทำงาน โดย environment variables ที่ตั้งใน shell/IDE จะมีลำดับความสำคัญสูงกว่า คัดลอก `.env.example` เป็นจุดเริ่มต้นได้ และห้าม commit ไฟล์ `.env`

เมนูหลักส่งเป็น Flex Message เมื่อ follow/พิมพ์ “เมนู”; ยังไม่ได้สร้าง Rich Menu แบบรูปภาพในบัญชี LINE หลังพนักงานเลือกน้ำหนักใน Dashboard ระบบคิดราคา 1 กก. = 80 บาท และเพิ่มกิโลละ 60 บาท (สูงสุด 6 กก.) แล้วส่งยอดพร้อมปุ่มเงินสด/PromptPay ให้ลูกค้าใน LINE เงินสดจะตั้งช่องทางชำระและปล่อยให้พนักงานเริ่มงานเอง ส่วน PromptPay จะรอรูปสลิปจากลูกค้าในแชต รูปสลิปแสดงอยู่ในรายละเอียดออเดอร์โดยไม่มีช่องอัปโหลดซ้ำใน Dashboard และตั้ง `payment_status` เป็น `pending_verification` เท่านั้น พนักงานต้องตรวจยอดจริงและยืนยันชำระเอง

สำหรับออเดอร์หน้าร้าน ใช้ปุ่มสร้างรหัสเชื่อมในหน้ารายละเอียดออเดอร์ แล้วให้ลูกค้าพิมพ์ `ผูก <รหัส>` ภายใน 15 นาที รหัสใช้ได้ครั้งเดียวและไม่สามารถผูกออเดอร์ที่เชื่อมกับ LINE อื่นแล้ว

ข้อควรทำก่อนใช้งานจริง: เพิ่ม CSRF protection ให้ฟอร์ม, ใช้ WSGI server แทน Flask development server, ตั้งการสำรองฐานข้อมูล/ไฟล์ upload และดูแลการ retry แจ้งเตือนเมื่อ LINE API ใช้งานไม่ได้
