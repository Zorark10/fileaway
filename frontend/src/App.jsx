import { useState, useRef, useCallback } from 'react'
import './index.css'

// ── API config ─────────────────────────────────────────────
const API_BASE = (
  import.meta.env.VITE_API_BASE_URL
  || import.meta.env.VITE_API_BASE
  || (
    typeof window !== 'undefined' && (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1')
      ? 'http://127.0.0.1:8000'
      : ''
  )
).replace(/\/+$/, '')

const MAX_FILE_SIZE = 100 * 1024 * 1024 // 100 MB

// ── Helpers ────────────────────────────────────────────────
class UploadError extends Error {
  constructor(message, status = 0) {
    super(message)
    this.name = 'UploadError'
    this.status = status
  }
}

function formatBytes(bytes) {
  if (!bytes || bytes === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(1024))
  return `${(bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1)} ${units[i]}`
}

async function readResponseError(response) {
  let detail = ''
  try {
    const data = await response.json()
    if (typeof data?.detail === 'string') detail = data.detail
  } catch {}
  return new UploadError(detail || `Request failed (${response.status}).`, response.status)
}

function xhrUpload({ method, url, body, headers = {}, onProgress, onXhr }) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    onXhr(xhr)

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded, event.total)
    }

    xhr.onload = () => {
      let data = null
      try {
        data = JSON.parse(xhr.responseText)
      } catch {}

      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(data)
        return
      }

      reject(new UploadError(
        data?.detail || `Upload failed (${xhr.status}).`,
        xhr.status,
      ))
    }

    xhr.onerror = () => {
      reject(new UploadError('Cannot reach the upload service.', 0))
    }

    xhr.onabort = () => {
      reject(new DOMException('Upload cancelled', 'AbortError'))
    }

    xhr.open(method, url)
    Object.entries(headers).forEach(([name, value]) => {
      xhr.setRequestHeader(name, value)
    })
    xhr.send(body)
  })
}

function errorMessage(error) {
  if (error?.status === 413) return 'File is too large (maximum 100 MB).'
  if (error?.status === 429) return 'Too many uploads. Please wait a moment and try again.'
  if (error?.status === 503) return 'The upload service is temporarily unavailable. Please try again.'
  if (error instanceof TypeError) return 'Cannot reach the upload service. Please try again.'
  if (error?.message) return error.message
  return 'Upload failed. Please try again.'
}

function formatExpiry(expiresAt) {
  if (!expiresAt) return ''
  try {
    // Backend returns "DD-MM-YYYY HH:MM:SS"
    const parts = expiresAt.trim().split(' ')
    if (parts.length < 2) return expiresAt
    const [day, month, year] = parts[0].split('-')
    const [hour, minute] = parts[1].split(':')
    const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    const monthIdx = parseInt(month, 10) - 1
    const monthName = months[monthIdx] || month
    return `${day} ${monthName} ${year}, ${hour}:${minute} UTC`
  } catch {
    return expiresAt
  }
}

function getFileMeta(filename) {
  if (!filename) return { ext: 'FILE', label: 'FILE', category: 'file' }
  const ext = filename.split('.').pop()?.toLowerCase() || ''
  if (['zip', 'tar', 'gz', '7z', 'rar', 'bz2'].includes(ext)) {
    return { ext: ext.toUpperCase(), label: 'ARCHIVE', category: 'archive' }
  }
  if (['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'avif'].includes(ext)) {
    return { ext: ext.toUpperCase(), label: 'IMAGE', category: 'image' }
  }
  if (['mp4', 'mov', 'webm', 'mkv', 'avi', 'mp3', 'wav', 'ogg', 'flac'].includes(ext)) {
    return { ext: ext.toUpperCase(), label: 'MEDIA', category: 'media' }
  }
  if (['pdf', 'doc', 'docx', 'txt', 'rtf', 'md', 'epub'].includes(ext)) {
    return { ext: ext.toUpperCase(), label: 'DOCUMENT', category: 'document' }
  }
  if (['msi', 'exe', 'dmg', 'pkg', 'deb', 'rpm', 'apk', 'iso'].includes(ext)) {
    return { ext: ext.toUpperCase(), label: 'INSTALLER', category: 'installer' }
  }
  if (['js', 'jsx', 'ts', 'tsx', 'py', 'json', 'html', 'css', 'sql', 'rs', 'go'].includes(ext)) {
    return { ext: ext.toUpperCase(), label: 'SOURCE', category: 'code' }
  }
  return { ext: ext ? ext.toUpperCase() : 'FILE', label: 'FILE', category: 'file' }
}

// ── Icons ──────────────────────────────────────────────────
function BrandMark({ className = 'w-6 h-6' }) {
  return (
    <svg aria-hidden="true" className={className} viewBox="0 0 32 32" fill="none">
      <rect width="32" height="32" rx="8" fill="#4E5B36" />
      <path d="M10 8h8l6 6v10a2 2 0 01-2 2H10a2 2 0 01-2-2V10a2 2 0 012-2z" fill="#FAF8F5" fillOpacity="0.95" />
      <path d="M18 8v6h6" stroke="#4E5B36" strokeWidth="1.5" strokeLinejoin="round" />
      <path d="M12 18h8M12 21h5" stroke="#4E5B36" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  )
}

function CategoryIcon({ category = 'file', className = 'w-5 h-5 text-accent' }) {
  if (category === 'archive') {
    return (
      <svg aria-hidden="true" className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M21 8v13H3V8M1 3h22v5H1zM10 12h4" />
      </svg>
    )
  }
  if (category === 'image') {
    return (
      <svg aria-hidden="true" className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="3" width="18" height="18" rx="2" />
        <circle cx="8.5" cy="8.5" r="1.5" />
        <polyline points="21 15 16 10 5 21" />
      </svg>
    )
  }
  if (category === 'media') {
    return (
      <svg aria-hidden="true" className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <rect x="2" y="2" width="20" height="20" rx="2.18" />
        <polygon points="10 8 16 12 10 16 10 8" fill="currentColor" fillOpacity="0.2" />
      </svg>
    )
  }
  if (category === 'installer') {
    return (
      <svg aria-hidden="true" className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" />
      </svg>
    )
  }
  if (category === 'code') {
    return (
      <svg aria-hidden="true" className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="16 18 22 12 16 6" />
        <polyline points="8 6 2 12 8 18" />
      </svg>
    )
  }
  return (
    <svg aria-hidden="true" className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" />
      <polyline points="14 2 14 8 20 8" />
      <line x1="16" y1="13" x2="8" y2="13" />
      <line x1="16" y1="17" x2="8" y2="17" />
    </svg>
  )
}

function IconUploadCloud({ className = 'w-6 h-6' }) {
  return (
    <svg aria-hidden="true" className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 16v-8M8 11l4-4 4 4" />
      <path d="M20.39 18.39A5 5 0 0018 9h-1.26A8 8 0 103 16.3" />
    </svg>
  )
}

function IconArrowUp({ className = 'w-4 h-4' }) {
  return (
    <svg aria-hidden="true" className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <line x1="12" y1="19" x2="12" y2="5" />
      <polyline points="5 12 12 5 19 12" />
    </svg>
  )
}

function IconCopy({ className = 'w-4 h-4' }) {
  return (
    <svg aria-hidden="true" className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <rect x="9" y="9" width="13" height="13" rx="2" />
      <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1" />
    </svg>
  )
}

function IconCheck({ className = 'w-4 h-4' }) {
  return (
    <svg aria-hidden="true" className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  )
}

function IconRefresh({ className = 'w-3.5 h-3.5' }) {
  return (
    <svg aria-hidden="true" className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21.5 2v6h-6M2.5 22v-6h6M2 11.5a10 10 0 0118.8-4.3M22 12.5a10 10 0 01-18.8 4.3" />
    </svg>
  )
}

function IconCross({ className = 'w-3.5 h-3.5' }) {
  return (
    <svg aria-hidden="true" className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="18" y1="6" x2="6" y2="18" />
      <line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  )
}

function IconExternal({ className = 'w-3.5 h-3.5' }) {
  return (
    <svg aria-hidden="true" className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6" />
      <polyline points="15 3 21 3 21 9" />
      <line x1="10" y1="14" x2="21" y2="3" />
    </svg>
  )
}

function IconAlertTriangle({ className = 'w-4 h-4' }) {
  return (
    <svg aria-hidden="true" className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
      <line x1="12" y1="9" x2="12" y2="13" />
      <line x1="12" y1="17" x2="12.01" y2="17" />
    </svg>
  )
}

function Spinner({ size = 16, className = '' }) {
  return (
    <svg
      role="status"
      aria-label="Loading"
      className={`animate-spin-slow ${className}`}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
    >
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="2.5" strokeOpacity="0.2" />
      <path d="M12 2a10 10 0 0110 10" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
    </svg>
  )
}

// ── State machine definition ───────────────────────────────
const STATES = {
  IDLE: 'idle',
  UPLOADING: 'uploading',
  SUCCESS: 'success',
  ERROR: 'error',
}

export default function App() {
  const [state, setState]                 = useState(STATES.IDLE)
  const [file, setFile]                   = useState(null)
  const [progress, setProgress]           = useState(0)
  const [uploadedBytes, setUploadedBytes] = useState(0)
  const [result, setResult]               = useState(null)
  const [error, setError]                 = useState('')
  const [copied, setCopied]               = useState(false)
  const [dragging, setDragging]           = useState(false)

  const inputRef   = useRef(null)
  const xhrRef     = useRef(null)
  const abortRef   = useRef(null)
  const cancelledRef = useRef(false)
  const copyTimer  = useRef(null)

  // ── File Selection ───────────────────────────────────────
  const selectFile = useCallback((f) => {
    if (!f) return
    if (f.size > MAX_FILE_SIZE) {
      setError('File exceeds the 100 MB limit. Please select a smaller file.')
      setState(STATES.ERROR)
      setFile(null)
      return
    }
    setFile(f)
    setError('')
    setState(STATES.IDLE)
    setResult(null)
    setCopied(false)
  }, [])

  // ── Drag & Drop Handlers ─────────────────────────────────
  const onDragOver = (e) => {
    e.preventDefault()
    e.stopPropagation()
    setDragging(true)
  }

  const onDragLeave = (e) => {
    e.preventDefault()
    e.stopPropagation()
    setDragging(false)
  }

  const onDrop = (e) => {
    e.preventDefault()
    e.stopPropagation()
    setDragging(false)
    const dropped = e.dataTransfer.files?.[0]
    if (dropped) selectFile(dropped)
  }

  // ── Upload: direct-to-B2 when enabled, proxy fallback otherwise ──
  const upload = useCallback(async () => {
    if (!file) return

    cancelledRef.current = false
    setState(STATES.UPLOADING)
    setProgress(0)
    setUploadedBytes(0)
    setError('')

    const controller = new AbortController()
    abortRef.current = controller

    const onProgress = (loaded, total) => {
      setUploadedBytes(loaded)
      if (total > 0) setProgress(Math.min(100, Math.round((loaded / total) * 100)))
    }

    try {
      const authorization = await fetch(`${API_BASE}/uploads/authorize`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          original_name: file.name,
          content_type: file.type || 'application/octet-stream',
          file_size: file.size,
        }),
        signal: controller.signal,
      })

      if (!authorization.ok) throw await readResponseError(authorization)

      const authorizationData = await authorization.json()
      let data

      if (authorizationData.mode === 'direct') {
        if (!authorizationData.upload_url || !authorizationData.upload_token) {
          throw new UploadError('The upload service returned an invalid authorization.')
        }

        await xhrUpload({
          method: authorizationData.method || 'PUT',
          url: authorizationData.upload_url,
          body: file,
          headers: authorizationData.headers || {},
          onProgress,
          onXhr: (xhr) => { xhrRef.current = xhr },
        })

        if (cancelledRef.current) return
        setProgress(100)
        setUploadedBytes(file.size)

        const completed = await fetch(`${API_BASE}/uploads/complete`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            upload_token: authorizationData.upload_token,
          }),
          signal: controller.signal,
        })

        if (!completed.ok) throw await readResponseError(completed)
        data = await completed.json()
      } else {
        const form = new FormData()
        form.append('uploaded_file', file)

        data = await xhrUpload({
          method: 'POST',
          url: `${API_BASE}/upload`,
          body: form,
          onProgress,
          onXhr: (xhr) => { xhrRef.current = xhr },
        })
      }

      if (cancelledRef.current) return

      setProgress(100)
      setUploadedBytes(file.size)
      setResult(data)
      setState(STATES.SUCCESS)
    } catch (uploadError) {
      if (cancelledRef.current || uploadError?.name === 'AbortError') return
      setError(errorMessage(uploadError))
      setState(STATES.ERROR)
    } finally {
      if (abortRef.current === controller) abortRef.current = null
      xhrRef.current = null
    }
  }, [file])

  // ── Cancel ongoing upload ────────────────────────────────
  const cancelUpload = useCallback(() => {
    cancelledRef.current = true
    abortRef.current?.abort()
    if (xhrRef.current) {
      xhrRef.current.abort()
      xhrRef.current = null
    }
    setState(STATES.IDLE)
    setProgress(0)
    setUploadedBytes(0)
  }, [])

  const fallbackCopy = useCallback((text) => {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    try {
      document.execCommand('copy')
      setCopied(true)
      clearTimeout(copyTimer.current)
      copyTimer.current = setTimeout(() => setCopied(false), 2200)
    } catch {}
    document.body.removeChild(ta)
  }, [])

  // ── Copy Share URL ───────────────────────────────────────
  const copyLink = useCallback(() => {
    if (!result?.share_url) return
    const url = result.share_url

    if (navigator?.clipboard?.writeText) {
      navigator.clipboard.writeText(url).then(() => {
        setCopied(true)
        clearTimeout(copyTimer.current)
        copyTimer.current = setTimeout(() => setCopied(false), 2200)
      }).catch(() => {
        fallbackCopy(url)
      })
    } else {
      fallbackCopy(url)
    }
  }, [result, fallbackCopy])

  // ── Reset to initial state ───────────────────────────────
  const reset = useCallback(() => {
    cancelledRef.current = true
    abortRef.current?.abort()
    if (xhrRef.current) {
      xhrRef.current.abort()
      xhrRef.current = null
    }
    setState(STATES.IDLE)
    setFile(null)
    setProgress(0)
    setUploadedBytes(0)
    setResult(null)
    setError('')
    setCopied(false)
    if (inputRef.current) inputRef.current.value = ''
  }, [])

  const isUploading = state === STATES.UPLOADING
  const isSuccess   = state === STATES.SUCCESS
  const isError     = state === STATES.ERROR
  const fileMeta    = file ? getFileMeta(file.name) : null

  return (
    <div className="min-h-screen bg-bg text-ink flex flex-col justify-between selection:bg-accent-muted selection:text-ink">

      {/* ── HEADER ───────────────────────────────────────────────── */}
      <header className="w-full border-b border-border/80 bg-surface/80 backdrop-blur-sm sticky top-0 z-20">
        <div className="max-w-[540px] mx-auto px-4 sm:px-6 h-13 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <BrandMark className="w-6 h-6" />
            <div className="flex items-baseline gap-2">
              <span className="font-medium text-sm tracking-tight text-ink">FileAway</span>
              <span className="font-mono text-[10px] text-subtle tracking-wider uppercase border border-border/80 px-1.5 py-0.5 rounded bg-surface-sub">
                Temporary Transit
              </span>
            </div>
          </div>

          <div className="flex items-center gap-2 text-xs text-subtle font-mono">
            <span className="inline-block w-1.5 h-1.5 rounded-full bg-accent animate-pulse-soft" />
            <span>24h Expiry &bull; 100 MB</span>
          </div>
        </div>
      </header>

      {/* ── MAIN WORKSPACE ─────────────────────────────────────────── */}
      <main className="flex-1 flex flex-col items-center justify-center px-4 sm:px-6 py-10 sm:py-16">
        <div className="w-full max-w-[540px]">

          {/* ── EDITORIAL MASTHEAD ──────────────────────────────────── */}
          <div className="mb-6 sm:mb-7 text-center sm:text-left">
            <div className="inline-flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-[0.16em] text-accent mb-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-accent" />
              <span>
                {isSuccess ? 'Dispatch Complete' : isUploading ? 'Uploading Payload' : 'File Sharing'}
              </span>
            </div>

            <h1 className="font-serif text-2xl sm:text-[28px] text-ink font-normal tracking-tight leading-snug">
              {isSuccess
                ? 'Your file is ready to share.'
                : file
                  ? 'Ready for upload.'
                  : 'Send files with quiet simplicity.'}
            </h1>

            <p className="text-muted text-xs sm:text-sm mt-1 leading-relaxed">
              {isSuccess
                ? 'Share this link with your recipient. The file will expire after 24 hours.'
                : file
                  ? 'Review the file information below before uploading.'
                  : 'Select or drop any file up to 100 MB. Files expire automatically after 24 hours.'}
            </p>
          </div>

          {/* ── UTILITY CARD ─────────────────────────────────────────── */}
          <div className="bg-surface border border-border rounded-xl shadow-card p-5 sm:p-6 relative overflow-hidden transition-[box-shadow,border-color] duration-200">

            {/* Subtle top brand hairline */}
            <div className="absolute top-0 left-0 right-0 h-[2px] bg-gradient-to-r from-accent/20 via-accent to-accent/20" />

            {/* ── INTEGRATED ERROR RIBBON ───────────────────────────── */}
            {isError && (
              <div
                role="alert"
                className="mb-5 flex items-start justify-between gap-3 px-3.5 py-3 bg-error-muted border border-error-border rounded-lg animate-fade-slide-up"
              >
                <div className="flex items-start gap-2.5 min-w-0">
                  <IconAlertTriangle className="text-error w-4 h-4 mt-0.5 shrink-0" />
                  <p className="text-xs sm:text-sm text-error leading-relaxed break-words">{error}</p>
                </div>
                <button
                  type="button"
                  onClick={() => setError('')}
                  aria-label="Dismiss error notification"
                  className="text-error/70 hover:text-error p-1 rounded hover:bg-error-border/40 transition-colors shrink-0 min-w-[28px] min-h-[28px] flex items-center justify-center cursor-pointer"
                >
                  <IconCross />
                </button>
              </div>
            )}

            {/* ── SUCCESS VIEW ───────────────────────────────────────── */}
            {isSuccess && result && (
              <div className="animate-fade-slide-up space-y-5">

                {/* File Details Summary Card */}
                <div className="p-3.5 bg-surface-sub border border-border rounded-lg flex items-center justify-between gap-3">
                  <div className="flex items-center gap-3 min-w-0">
                    <div className="w-8 h-8 rounded-full bg-success-muted border border-success-border flex items-center justify-center text-success shrink-0">
                      <IconCheck className="w-4 h-4 stroke-[2.5]" />
                    </div>
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-ink truncate" title={result.original_name}>
                        {result.original_name}
                      </p>
                      <div className="flex items-center gap-2 mt-0.5 text-xs text-muted font-mono">
                        <span>{formatBytes(result.file_size)}</span>
                        {result.content_type && (
                          <>
                            <span className="text-subtle">&bull;</span>
                            <span className="text-subtle truncate max-w-[140px] sm:max-w-none">{result.content_type}</span>
                          </>
                        )}
                      </div>
                    </div>
                  </div>

                  <span className="shrink-0 font-mono text-[11px] text-success font-medium bg-success-muted px-2 py-0.5 rounded border border-success-border">
                    Uploaded
                  </span>
                </div>

                {/* Share URL Box */}
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <label htmlFor="share-url-input" className="block font-mono text-[11px] uppercase tracking-wider text-muted">
                      Public Share Link
                    </label>
                    <span className="font-mono text-[10px] text-subtle">Full URL</span>
                  </div>

                  <div className="flex flex-col sm:flex-row gap-2">
                    <input
                      id="share-url-input"
                      type="url"
                      readOnly
                      value={result.share_url}
                      onFocus={(e) => e.target.select()}
                      className="flex-1 min-w-0 bg-surface-sub border border-border rounded-lg px-3.5 py-2.5 text-xs sm:text-sm font-mono text-ink tracking-tight select-all focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent"
                      aria-label="Generated Share Link"
                    />

                    <button
                      id="copy-link-btn"
                      type="button"
                      onClick={copyLink}
                      aria-live="polite"
                      className={`
                        btn-press shrink-0 flex items-center justify-center gap-2 px-5 py-2.5 rounded-lg text-xs sm:text-sm font-medium
                        border cursor-pointer min-h-[42px]
                        ${copied
                          ? 'bg-accent-muted text-accent border-accent-border'
                          : 'bg-accent text-surface border-transparent hover:bg-accent-hover shadow-subtle'
                        }
                      `}
                    >
                      {copied ? <IconCheck /> : <IconCopy />}
                      <span>{copied ? 'Copied' : 'Copy link'}</span>
                    </button>
                  </div>
                </div>

                {/* Expiry Timestamp & Direct Download Test */}
                <div className="p-3 bg-surface-sub border border-border-sub rounded-lg flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-xs text-muted">
                  <div className="flex items-center gap-1.5 min-w-0">
                    <span className="text-subtle font-mono text-[11px] uppercase tracking-wider">Expires:</span>
                    <span className="font-mono font-medium text-ink whitespace-nowrap">{formatExpiry(result.expires_at)}</span>
                  </div>

                  <a
                    href={result.share_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 text-accent hover:text-accent-hover font-medium underline underline-offset-4 shrink-0 text-xs"
                  >
                    <span>Test download link</span>
                    <IconExternal />
                  </a>
                </div>

                {/* Reset Action */}
                <button
                  id="share-another-btn"
                  type="button"
                  onClick={reset}
                  className="btn-press w-full flex items-center justify-center gap-2 py-2.5 px-4 rounded-lg border border-border bg-surface text-xs sm:text-sm font-medium text-muted hover:text-ink hover:border-ink/30 hover:bg-surface-sub transition-colors cursor-pointer min-h-[42px]"
                >
                  <IconRefresh />
                  <span>Send another file</span>
                </button>
              </div>
            )}

            {/* ── WORKSPACE: IDLE / SELECTED / UPLOADING ────────────── */}
            {!isSuccess && (
              <div className="space-y-4">

                {/* ── STATE 1: UPLOADING ── */}
                {isUploading ? (
                  <div className="py-6 px-4 sm:px-6 bg-surface-sub border border-border rounded-lg space-y-4 animate-fade-slide-up">
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex items-center gap-3 min-w-0">
                        <div className="w-9 h-9 rounded-lg bg-accent-muted flex items-center justify-center shrink-0">
                          <CategoryIcon category={fileMeta?.category} className="w-5 h-5 text-accent" />
                        </div>
                        <div className="min-w-0">
                          <p className="text-sm font-medium text-ink truncate" title={file?.name}>
                            {file?.name}
                          </p>
                          <p className="font-mono text-xs text-subtle mt-0.5">
                            {formatBytes(uploadedBytes)} of {formatBytes(file?.size)}
                          </p>
                        </div>
                      </div>

                      <div className="font-mono text-base font-semibold text-accent shrink-0">
                        {progress}%
                      </div>
                    </div>

                    {/* Progress Bar */}
                    <div
                      className="w-full bg-border-sub rounded-full h-2 overflow-hidden"
                      role="progressbar"
                      aria-valuenow={progress}
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-label="Upload progress"
                    >
                      <div
                        className="h-full bg-accent rounded-full transition-[width] duration-150 ease-out-strong"
                        style={{ width: `${progress}%` }}
                      />
                    </div>

                    <div className="flex items-center justify-between text-xs text-subtle pt-1 font-mono">
                      <span className="flex items-center gap-1.5">
                        <Spinner size={12} className="text-accent" />
                        Uploading to server...
                      </span>
                      <button
                        type="button"
                        onClick={cancelUpload}
                        className="text-muted hover:text-error transition-colors cursor-pointer"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                ) : file ? (

                  /* ── STATE 2: FILE SELECTED (FILE DOSSIER) ── */
                  <div className="space-y-4 animate-fade-slide-up">
                    <div className="p-4 bg-surface-sub border border-border rounded-lg flex items-center justify-between gap-3">
                      <div className="flex items-center gap-3 min-w-0">
                        <div className="w-10 h-10 rounded-lg bg-accent-muted border border-accent-border/40 flex items-center justify-center shrink-0">
                          <CategoryIcon category={fileMeta?.category} className="w-5 h-5 text-accent" />
                        </div>

                        <div className="min-w-0">
                          <p className="text-sm font-medium text-ink truncate" title={file.name}>
                            {file.name}
                          </p>
                          <div className="flex items-center gap-2 mt-0.5 text-xs text-muted font-mono">
                            <span>{formatBytes(file.size)}</span>
                            <span className="text-subtle">&bull;</span>
                            <span className="uppercase text-[11px] text-subtle">{fileMeta?.ext || 'FILE'}</span>
                            <span className="text-subtle">&bull;</span>
                            <span className="text-subtle text-[11px]">24h expiry</span>
                          </div>
                        </div>
                      </div>

                      <div className="flex items-center gap-1.5 shrink-0">
                        <button
                          type="button"
                          onClick={() => inputRef.current?.click()}
                          aria-label="Change selected file"
                          className="btn-press px-2.5 py-1.5 rounded-md border border-border text-xs text-muted hover:text-ink hover:border-ink/30 bg-surface flex items-center gap-1 cursor-pointer min-h-[36px]"
                        >
                          <IconRefresh />
                          <span>Change</span>
                        </button>
                        <button
                          type="button"
                          onClick={() => { setFile(null); if (inputRef.current) inputRef.current.value = '' }}
                          aria-label="Remove selected file"
                          className="btn-press p-2 rounded-md border border-border text-xs text-subtle hover:text-error hover:border-error/30 bg-surface cursor-pointer min-h-[36px] min-w-[36px] flex items-center justify-center"
                        >
                          <IconCross />
                        </button>
                      </div>
                    </div>

                    {/* Prominent Upload Action */}
                    <button
                      id="upload-btn"
                      type="button"
                      onClick={upload}
                      className="btn-press w-full flex items-center justify-center gap-2 py-3 px-5 rounded-lg text-sm font-medium bg-accent text-surface hover:bg-accent-hover shadow-subtle cursor-pointer min-h-[44px]"
                    >
                      <IconArrowUp className="w-4 h-4" />
                      <span>Upload file ({formatBytes(file.size)})</span>
                    </button>
                  </div>
                ) : (

                  /* ── STATE 3: IDLE DROP WELL ── */
                  <div
                    id="upload-dropzone"
                    role="button"
                    tabIndex={0}
                    aria-label="Click to browse or drag and drop a file"
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        inputRef.current?.click()
                      }
                    }}
                    onDragOver={onDragOver}
                    onDragLeave={onDragLeave}
                    onDrop={onDrop}
                    onClick={() => inputRef.current?.click()}
                    className={`
                      relative p-8 sm:p-10 border-2 border-dashed rounded-xl cursor-pointer text-center group
                      transition-[border-color,background-color] duration-150 ease-out-strong
                      ${dragging
                        ? 'dropzone-active cursor-copy'
                        : 'border-border bg-surface-sub/50 hover:bg-accent-muted/30 hover:border-accent/60'
                      }
                    `}
                  >
                    <div className="flex flex-col items-center justify-center gap-3">
                      <div className="w-11 h-11 rounded-xl bg-surface border border-border/80 shadow-subtle flex items-center justify-center text-accent group-hover:scale-105 group-hover:border-accent/40 transition-transform duration-150">
                        <IconUploadCloud className="w-5 h-5 text-accent" />
                      </div>

                      <div>
                        <p className="text-sm sm:text-base font-medium text-ink">
                          <span className="text-accent underline underline-offset-4 decoration-accent/40 group-hover:decoration-accent">
                            Browse file
                          </span>{' '}
                          or drag & drop here
                        </p>
                        <p className="font-mono text-xs text-subtle mt-1.5">
                          Up to 100 MB &bull; Files expire after 24 hours
                        </p>
                      </div>
                    </div>
                  </div>
                )}

                {/* Hidden File Input */}
                <input
                  ref={inputRef}
                  id="file-input"
                  type="file"
                  aria-label="Choose file to upload"
                  className="sr-only"
                  onChange={(e) => {
                    const f = e.target.files?.[0]
                    if (f) selectFile(f)
                  }}
                  tabIndex={-1}
                />

              </div>
            )}
          </div>

          {/* ── FOOTER ──────────────────────────────────────────────── */}
          <div className="mt-6 sm:mt-7 pt-4 border-t border-border/80 flex flex-col sm:flex-row items-center justify-between gap-2.5 text-xs text-subtle font-mono">
            <div className="flex items-center gap-2">
              <span className="w-1.5 h-1.5 rounded-full bg-accent/70" />
              <span>FileAway</span>
            </div>

            <div className="flex items-center gap-3 text-[11px] text-subtle text-center sm:text-right">
              <span>Files expire after 24 hours</span>
              <span>&bull;</span>
              <span>Maximum 100 MB</span>
            </div>
          </div>

        </div>
      </main>

    </div>
  )
}
