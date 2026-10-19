"""Private-chat PDF ingestion. No filesystem paths from names, no automatic generation."""
import asyncio
from services import practice
from services.practice_pdf import read_pdf, MAX_BYTES
from services.practice_store import PracticeStore
from services.conversation_telegram import private_user, reply


async def handle_pdf(update,context):
    user=private_user(update)
    if user is None:return
    if not practice.enabled():
        await reply(update,'Document practice is not enabled yet.');return
    document=update.message.document
    if (document.mime_type!='application/pdf' or not document.file_name or
            not document.file_name.lower().endswith('.pdf') or not document.file_size or document.file_size>MAX_BYTES):
        await reply(update,'Send a text-based PDF up to 5 MB, or paste study notes. Other formats and OCR are not supported.');return
    try:
        remote=await document.get_file()
        # A declared-size check is not a substitute for the actual transfer limit.
        from io import BytesIO
        class LimitedBuffer(BytesIO):
            def write(self,data):
                if self.tell()+len(data)>MAX_BYTES:raise ValueError('File limit exceeded.')
                return super().write(data)
        buffer=LimitedBuffer()
        await remote.download_to_memory(out=buffer)
        data=await asyncio.to_thread(read_pdf,buffer.getvalue())
        result=await asyncio.to_thread(PracticeStore(user).add_material,document.file_name[:160],data['text'],
            'telegram-pdf:'+str(update.message.message_id),'pdf',data['warnings'])
    except Exception:
        await reply(update,'I could not read that PDF. It may be scanned, encrypted or too complex. Paste its text instead; no quiz was generated.');return
    await reply(update,'Saved your PDF as private study material: '+result['title']+
        '. Ask for an explanation or practice on a section, or open Generated Practice in the Mini App. '+
        ('Some content could not be fully extracted; check the source warnings there.' if data['warnings'] else ''))
