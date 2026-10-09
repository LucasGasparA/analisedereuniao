(() => {
  const form = document.querySelector('#media-upload-form');
  const jobsSection = document.querySelector('#jobs-section');
  if (!form || !jobsSection) return;

  const fileInput = document.querySelector('#media-file');
  const fileDrop = document.querySelector('#file-drop');
  const fileLabel = document.querySelector('#file-label');
  const button = document.querySelector('#upload-button');
  const feedback = document.querySelector('#upload-feedback');
  const uploadTitle = document.querySelector('#upload-title');
  const uploadPercent = document.querySelector('#upload-percent');
  const uploadBar = document.querySelector('#upload-bar');
  const uploadDetail = document.querySelector('#upload-detail');
  const jobsList = document.querySelector('#jobs-list');
  const liveIndicator = document.querySelector('#live-indicator');
  const jobsUrl = jobsSection.dataset.jobsUrl;
  const defaultFileLabel = fileLabel.textContent;
  let pollTimer = null;

  const formatBytes = (bytes) => {
    if (!bytes) return '0 bytes';
    const units = ['bytes', 'KB', 'MB', 'GB'];
    const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    const value = bytes / (1024 ** index);
    return `${value.toFixed(index > 1 ? 1 : 0)} ${units[index]}`;
  };

  const showSelectedFile = () => {
    const file = fileInput.files[0];
    if (file) fileLabel.textContent = `${file.name} · ${formatBytes(file.size)}`;
  };

  fileInput.addEventListener('change', showSelectedFile);
  ['dragenter', 'dragover'].forEach((eventName) => fileDrop.addEventListener(eventName, (event) => {
    event.preventDefault();
    fileDrop.classList.add('dragging');
  }));
  ['dragleave', 'drop'].forEach((eventName) => fileDrop.addEventListener(eventName, (event) => {
    event.preventDefault();
    fileDrop.classList.remove('dragging');
  }));
  fileDrop.addEventListener('drop', (event) => {
    if (!event.dataTransfer.files.length) return;
    fileInput.files = event.dataTransfer.files;
    showSelectedFile();
  });

  const addText = (parent, tag, text, className) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    element.textContent = text;
    parent.appendChild(element);
    return element;
  };

  const statusLabel = (status) => ({
    na_fila: 'Na fila',
    processando: 'Processando',
    concluido: 'Concluída',
    erro: 'Erro'
  }[status] || status);

  const renderJobs = (jobs) => {
    jobsList.replaceChildren();
    if (!jobs.length) {
      addText(jobsList, 'div', 'Nenhuma transcrição iniciada para este liderado.', 'empty-inline');
      return false;
    }

    let hasActive = false;
    jobs.forEach((job) => {
      hasActive ||= job.status === 'na_fila' || job.status === 'processando';
      const card = document.createElement('article');
      card.className = `job-card status-${job.status}`;
      card.dataset.jobId = job.id;

      const top = document.createElement('div');
      top.className = 'job-topline';
      const identity = document.createElement('div');
      addText(identity, 'b', job.title);
      addText(identity, 'small', `${job.filename} · Perfil ${job.profile_label || 'Equilibrado'}`);
      top.appendChild(identity);
      addText(top, 'span', statusLabel(job.status), 'job-status');
      card.appendChild(top);

      const track = document.createElement('div');
      track.className = 'progress-track';
      const fill = document.createElement('span');
      fill.className = 'progress-fill';
      const progress = Math.max(0, Math.min(100, Number(job.progress || 0)));
      fill.style.width = `${progress}%`;
      track.appendChild(fill);
      card.appendChild(track);

      const bottom = document.createElement('div');
      bottom.className = 'job-bottom';
      addText(bottom, 'span', job.detail || 'Aguardando atualização');
      addText(bottom, 'b', job.status === 'erro' ? 'Falhou' : `${progress}%`, 'job-percent');
      card.appendChild(bottom);

      const actions = document.createElement('div');
      actions.className = 'job-actions';
      if (job.meeting_id) {
        const link = addText(actions, 'a', 'Abrir transcrição ›', 'job-link');
        link.href = `/meetings/${job.meeting_id}`;
      } else if (job.status === 'erro' && job.retryable) {
        const retry = addText(actions, 'button', '↻ Tentar novamente', 'retry-button');
        retry.type = 'button';
        retry.dataset.retryUrl = `/transcription-jobs/${job.id}/retry`;
      } else if (job.status === 'erro') {
        const reupload = addText(actions, 'button', '↑ Selecionar arquivo novamente', 'reupload-button');
        reupload.type = 'button';
      }
      card.appendChild(actions);
      jobsList.appendChild(card);
    });
    return hasActive;
  };

  const schedulePoll = (delay = 1800) => {
    window.clearTimeout(pollTimer);
    pollTimer = window.setTimeout(refreshJobs, delay);
  };

  const refreshJobs = async () => {
    try {
      const response = await fetch(jobsUrl, { headers: { Accept: 'application/json' } });
      if (!response.ok) throw new Error('Não foi possível atualizar o andamento.');
      const payload = await response.json();
      const hasActive = renderJobs(payload.jobs || []);
      liveIndicator.innerHTML = '<i></i> Atualizado agora';
      if (hasActive) schedulePoll();
    } catch (error) {
      liveIndicator.textContent = 'Tentando reconectar…';
      schedulePoll(5000);
    }
  };

  jobsList.addEventListener('click', async (event) => {
    const reuploadButton = event.target.closest('.reupload-button');
    if (reuploadButton) {
      form.scrollIntoView({ behavior: 'smooth', block: 'start' });
      window.setTimeout(() => fileInput.click(), 250);
      return;
    }
    const retryButton = event.target.closest('.retry-button');
    if (!retryButton) return;
    const token = form.querySelector('input[name="token"]').value;
    retryButton.disabled = true;
    retryButton.textContent = 'Reiniciando…';
    try {
      const response = await fetch(retryButton.dataset.retryUrl, {
        method: 'POST',
        headers: {
          'X-Requested-With': 'XMLHttpRequest',
          'Content-Type': 'application/x-www-form-urlencoded',
          Accept: 'application/json'
        },
        body: new URLSearchParams({ token })
      });
      if (!response.ok) {
        let message = 'Não foi possível reiniciar.';
        try { message = (await response.json()).detail || message; } catch (_) {}
        throw new Error(message);
      }
      await refreshJobs();
    } catch (error) {
      retryButton.disabled = false;
      retryButton.textContent = '↻ Tentar novamente';
      const detail = retryButton.closest('.job-card').querySelector('.job-bottom span');
      if (detail) detail.textContent = error.message;
    }
  });

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    if (!form.reportValidity()) return;

    button.disabled = true;
    button.textContent = 'Enviando…';
    feedback.hidden = false;
    feedback.classList.remove('error');
    uploadTitle.textContent = 'Enviando arquivo';
    uploadPercent.textContent = '0%';
    uploadBar.classList.remove('indeterminate');
    uploadBar.style.width = '0%';
    uploadDetail.textContent = 'Mantenha esta página aberta até o envio terminar.';

    const request = new XMLHttpRequest();
    request.open('POST', form.action);
    request.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
    request.setRequestHeader('Accept', 'application/json');

    request.upload.addEventListener('progress', (progressEvent) => {
      if (!progressEvent.lengthComputable) {
        uploadBar.classList.add('indeterminate');
        uploadPercent.textContent = 'Enviando';
        return;
      }
      const percent = Math.min(100, Math.round((progressEvent.loaded / progressEvent.total) * 100));
      uploadBar.classList.remove('indeterminate');
      uploadBar.style.width = `${percent}%`;
      uploadPercent.textContent = `${percent}%`;
      uploadDetail.textContent = `${formatBytes(progressEvent.loaded)} de ${formatBytes(progressEvent.total)} enviados`;
    });

    request.upload.addEventListener('load', () => {
      uploadTitle.textContent = 'Arquivo recebido';
      uploadDetail.textContent = 'Registrando o processamento no computador…';
    });

    request.addEventListener('load', () => {
      button.disabled = false;
      button.textContent = 'Enviar e transcrever';
      if (request.status >= 200 && request.status < 300) {
        uploadTitle.textContent = 'Envio concluído';
        uploadPercent.textContent = '100%';
        uploadBar.style.width = '100%';
        uploadDetail.textContent = 'A transcrição começou. Você pode acompanhar abaixo e até sair desta página.';
        form.reset();
        fileLabel.textContent = defaultFileLabel;
        refreshJobs();
        window.setTimeout(() => { feedback.hidden = true; }, 5500);
        return;
      }
      let message = 'Não foi possível enviar o arquivo.';
      try { message = JSON.parse(request.responseText).detail || message; } catch (_) {}
      feedback.classList.add('error');
      uploadTitle.textContent = 'Falha no envio';
      uploadPercent.textContent = '';
      uploadDetail.textContent = message;
    });

    request.addEventListener('error', () => {
      button.disabled = false;
      button.textContent = 'Tentar novamente';
      feedback.classList.add('error');
      uploadTitle.textContent = 'Conexão interrompida';
      uploadPercent.textContent = '';
      uploadDetail.textContent = 'O arquivo não foi enviado. Verifique se a aplicação continua aberta e tente novamente.';
    });

    request.send(new FormData(form));
  });

  refreshJobs();
})();
