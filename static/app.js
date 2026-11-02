(() => {
  const MAX_BYTES = 10 * 1024 * 1024;
  const TYPES = ["image/jpeg", "image/png", "image/webp", "image/bmp"];
  const $ = (id) => document.getElementById(id);
  const fileInput = $("fileInput"), dropZone = $("dropZone"), preview = $("preview"), overlay = $("overlay");
  const btn = $("analyzeBtn"), errBox = $("error");
  let currentFile = null, lastFace = null, objectUrl = null;

  const LABELS = { symmetry: "얼굴 대칭성", proportion: "얼굴 비율 균형", style: "스타일/사진 연출", skin: "피부톤 균일도", pose: "자세(고개 기울기)" };
  const METRIC_LABELS = {
    roll_degrees: "고개 기울기(°)", geometric_symmetry: "특징점 대칭", pixel_symmetry: "픽셀 좌우 대칭",
    eye_mouth_ratio: "눈~입 / 눈 사이 비율", mouth_eye_ratio: "입 너비 / 눈 사이 비율",
    face_brightness: "얼굴 밝기(0~255)", detection_confidence: "얼굴 검출 신뢰도",
    sharpness: "선명도", exposure: "노출", contrast: "대비", composition: "구도",
  };

  function showError(msg) { errBox.textContent = msg; errBox.classList.remove("hidden"); }
  function clearError() { errBox.classList.add("hidden"); errBox.textContent = ""; }
  function setState(s) {
    $("placeholder").classList.toggle("hidden", s !== "idle");
    $("loading").classList.toggle("hidden", s !== "loading");
    $("result").classList.toggle("hidden", s !== "done");
  }

  function selectFile(file) {
    clearError(); setState("idle"); lastFace = null; drawOverlay();
    if (!file) return;
    if (!TYPES.includes(file.type)) {
      currentFile = null; btn.disabled = true;
      return showError(`지원하지 않는 파일 형식입니다 (${file.type || "알 수 없음"}). JPG, PNG, WEBP, BMP만 가능합니다.`);
    }
    if (file.size > MAX_BYTES) {
      currentFile = null; btn.disabled = true;
      return showError("파일이 너무 큽니다 (최대 10MB).");
    }
    currentFile = file;
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = URL.createObjectURL(file);
    preview.onerror = () => { showError("미리보기를 표시할 수 없는 이미지입니다."); btn.disabled = true; };
    preview.onload = () => { drawOverlay(); };
    preview.src = objectUrl;
    $("previewWrap").classList.remove("hidden");
    $("fileInfo").textContent = `${file.name} · ${(file.size / 1024).toFixed(0)} KB`;
    btn.disabled = false;
  }

  // 미리보기 위에 얼굴 박스와 특징점 표시
  function drawOverlay() {
    const r = preview.getBoundingClientRect();
    overlay.width = r.width; overlay.height = r.height;
    overlay.style.width = r.width + "px"; overlay.style.height = r.height + "px";
    const ctx = overlay.getContext("2d");
    ctx.clearRect(0, 0, overlay.width, overlay.height);
    if (!lastFace || !preview.naturalWidth) return;
    // object-fit: contain 영역 계산
    const s = Math.min(r.width / preview.naturalWidth, r.height / preview.naturalHeight);
    const w = preview.naturalWidth * s, h = preview.naturalHeight * s;
    const ox = (r.width - w) / 2, oy = (r.height - h) / 2;
    const [bx, by, bw, bh] = lastFace.box;
    ctx.strokeStyle = "#7cf5c6"; ctx.lineWidth = 2;
    ctx.strokeRect(ox + bx * w, oy + by * h, bw * w, bh * h);
    ctx.fillStyle = "#ff4fa3";
    for (const [lx, ly] of lastFace.landmarks) {
      ctx.beginPath(); ctx.arc(ox + lx * w, oy + ly * h, 4, 0, Math.PI * 2); ctx.fill();
    }
  }
  window.addEventListener("resize", drawOverlay);

  fileInput.addEventListener("change", () => selectFile(fileInput.files[0]));
  ["dragenter", "dragover"].forEach((e) => dropZone.addEventListener(e, (ev) => { ev.preventDefault(); dropZone.classList.add("over"); }));
  ["dragleave", "drop"].forEach((e) => dropZone.addEventListener(e, (ev) => { ev.preventDefault(); dropZone.classList.remove("over"); }));
  dropZone.addEventListener("drop", (ev) => selectFile(ev.dataTransfer.files[0]));

  btn.addEventListener("click", async () => {
    if (!currentFile) return;
    clearError(); setState("loading"); btn.disabled = true;
    const fd = new FormData(); fd.append("file", currentFile);
    try {
      const res = await fetch("/api/analyze", { method: "POST", body: fd });
      let data;
      try { data = await res.json(); } catch { throw new Error(`서버 응답을 해석할 수 없습니다 (HTTP ${res.status}).`); }
      if (!res.ok || !data.ok) throw new Error(data.error || `분석 실패 (HTTP ${res.status})`);
      render(data);
    } catch (e) {
      setState("idle");
      showError(e instanceof TypeError ? "서버에 연결할 수 없습니다. 앱(서버)이 실행 중인지 확인하세요." : e.message);
    } finally {
      btn.disabled = !currentFile;
    }
  });

  function render(d) {
    setState("done");
    $("totalScore").textContent = d.total_score.toFixed(1);
    $("ring").style.setProperty("--p", d.total_score);
    $("grade").textContent = d.grade;
    const bars = $("bars"); bars.innerHTML = "";
    for (const [k, v] of Object.entries(d.scores)) {
      const el = document.createElement("div"); el.className = "bar";
      el.innerHTML = `<div class="bar-label"><span></span><b></b></div><div class="bar-track"><div class="bar-fill"></div></div>`;
      el.querySelector("span").textContent = LABELS[k] || k;
      el.querySelector("b").textContent = `${v.toFixed(1)} / 100`;
      bars.appendChild(el);
      requestAnimationFrame(() => { el.querySelector(".bar-fill").style.width = v + "%"; });
    }
    const ul = $("report"); ul.innerHTML = "";
    d.report.forEach((t) => { const li = document.createElement("li"); li.textContent = t; ul.appendChild(li); });
    const m = $("metrics"); m.innerHTML = "";
    const all = { ...d.style_details, ...d.metrics, faces_detected: d.faces_detected, elapsed_ms: d.elapsed_ms };
    const extra = { faces_detected: "감지된 얼굴 수", elapsed_ms: "분석 시간(ms)" };
    for (const [k, v] of Object.entries(all)) {
      const a = document.createElement("span"); a.textContent = METRIC_LABELS[k] || extra[k] || k;
      const b = document.createElement("b"); b.textContent = v === null ? "-" : v;
      m.append(a, b);
    }
    $("resultDisclaimer").textContent = "⚠ " + d.disclaimer;
    lastFace = d.face; drawOverlay();
  }
})();
