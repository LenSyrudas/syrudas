import { afterEach, describe, expect, it, vi } from 'vitest'
import { copyToClipboard } from './clipboard'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

/** Replace navigator.clipboard, which jsdom does not provide by default. */
function stubClipboard(writeText: unknown) {
  vi.stubGlobal('navigator', { ...navigator, clipboard: writeText ? { writeText } : undefined })
}

describe('copyToClipboard', () => {
  it('uses the async Clipboard API when it is available', async () => {
    const writeText = vi.fn(() => Promise.resolve())
    stubClipboard(writeText)
    await expect(copyToClipboard('hello')).resolves.toBe(true)
    expect(writeText).toHaveBeenCalledWith('hello')
  })

  // the desktop webview and any non-secure context reject writeText; the copy
  // buttons must still work there rather than silently doing nothing
  it('falls back to execCommand when the Clipboard API rejects', async () => {
    stubClipboard(vi.fn(() => Promise.reject(new Error('NotAllowedError'))))
    const exec = vi.fn(() => true)
    document.execCommand = exec as unknown as typeof document.execCommand
    await expect(copyToClipboard('fallback text')).resolves.toBe(true)
    expect(exec).toHaveBeenCalledWith('copy')
  })

  it('falls back when the Clipboard API is missing entirely', async () => {
    stubClipboard(undefined)
    const exec = vi.fn(() => true)
    document.execCommand = exec as unknown as typeof document.execCommand
    await expect(copyToClipboard('x')).resolves.toBe(true)
  })

  it('reports failure when both paths fail', async () => {
    stubClipboard(undefined)
    document.execCommand = vi.fn(() => false) as unknown as typeof document.execCommand
    await expect(copyToClipboard('x')).resolves.toBe(false)
  })

  it('leaves no scratch textarea behind after the fallback', async () => {
    stubClipboard(undefined)
    document.execCommand = vi.fn(() => true) as unknown as typeof document.execCommand
    await copyToClipboard('x')
    expect(document.querySelectorAll('textarea')).toHaveLength(0)
  })

  it('removes the scratch textarea even when execCommand throws', async () => {
    stubClipboard(undefined)
    document.execCommand = vi.fn(() => {
      throw new Error('boom')
    }) as unknown as typeof document.execCommand
    await expect(copyToClipboard('x')).rejects.toThrow('boom')
    expect(document.querySelectorAll('textarea')).toHaveLength(0)
  })
})
