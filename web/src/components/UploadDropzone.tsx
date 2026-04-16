import { useRef, useState } from 'react'

interface Props {
  onFiles: (files: File[]) => void
  disabled?: boolean
}

const ACCEPT = '.srt,.vtt'

export default function UploadDropzone({ onFiles, disabled }: Props) {
  const [over, setOver] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const handle = (files: FileList | null) => {
    if (!files || files.length === 0) return
    const arr = Array.from(files).filter((f) => /\.(srt|vtt)$/i.test(f.name))
    if (arr.length > 0) onFiles(arr)
  }

  return (
    <div
      className={`dropzone${over ? ' active' : ''}`}
      onClick={() => !disabled && inputRef.current?.click()}
      onDragOver={(e) => {
        e.preventDefault()
        if (!disabled) setOver(true)
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault()
        setOver(false)
        if (disabled) return
        handle(e.dataTransfer.files)
      }}
    >
      {disabled
        ? 'Pick a target language before uploading'
        : 'Drop .srt / .vtt files here, or click to browse'}
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={ACCEPT}
        style={{ display: 'none' }}
        onChange={(e) => {
          handle(e.target.files)
          e.target.value = ''
        }}
      />
    </div>
  )
}
