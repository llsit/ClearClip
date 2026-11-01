const form = document.querySelector('#download-form');
const button = document.querySelector('#submit');
const result = document.querySelector('#result');
const statusText = document.querySelector('#status');
const progress = document.querySelector('#progress');
const save = document.querySelector('#save');
const formatSelect = document.querySelector('#format');
const formatNotes = {
  mp4: 'MP4: เก็บไฟล์ต้นทางพร้อมเสียง ไม่บีบอัดซ้ำ',
  mov: 'MOV: เปลี่ยนคอนเทนเนอร์โดยไม่บีบอัดซ้ำ รองรับการเปิดตาม codec ของต้นทาง',
  avi: 'AVI: แปลงภาพและเสียงใหม่ อาจใช้เวลานาน ไฟล์ใหญ่ขึ้นและคุณภาพอาจลดลง',
};
formatSelect.addEventListener('change', () => {
  document.querySelector('#format-note').textContent = formatNotes[formatSelect.value];
});
const token = document.querySelector('meta[name="app-token"]').content;
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers: {'Content-Type':'application/json', 'X-App-Token':token}, signal: AbortSignal.timeout(30000)});
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || 'ไม่สามารถติดต่อเซิร์ฟเวอร์ได้');
  return body;
}
form.addEventListener('submit', async event => {
  event.preventDefault();
  button.disabled = true;
  formatSelect.disabled = true;
  result.hidden = false;
  result.classList.remove('error');
  save.hidden = true;
  document.querySelector('#title').textContent = '';
  document.querySelector('#details').textContent = '';
  progress.hidden = false;
  progress.removeAttribute('value');
  statusText.textContent = 'กำลังสร้างรายการดาวน์โหลด…';
  try {
    const {id} = await api('/api/jobs', {method:'POST', body:JSON.stringify({url:form.elements.url.value.trim(), format:formatSelect.value})});
    for (;;) {
      const job = await api(`/api/jobs/${id}`);
      statusText.textContent = job.message;
      if (job.percent == null) progress.removeAttribute('value'); else progress.value = job.percent;
      document.querySelector('#title').textContent = job.title || '';
      document.querySelector('#details').textContent = [job.creator && `@${job.creator}`, job.resolution, job.bytes && `${(job.bytes / 1048576).toFixed(1)} MB`].filter(Boolean).join(' · ');
      if (job.state === 'error') throw new Error(job.message + (job.error_code ? ` (${job.error_code})` : ''));
      if (job.state === 'ready') {
        save.href = `/files/${id}`;
        save.textContent = `บันทึกไฟล์ ${(job.output_format || 'mp4').toUpperCase()} ↓`;
        save.hidden = false;
        break;
      }
      await pause(1200);
    }
  } catch (error) {
    result.classList.add('error');
    statusText.textContent = error.name === 'TypeError' ? 'ติดต่อแอปไม่ได้ กรุณาตรวจสอบว่าหน้าต่างเซิร์ฟเวอร์ยังเปิดอยู่' : error.message;
    progress.hidden = true;
  } finally {
    button.disabled = false;
    formatSelect.disabled = false;
  }
});
