import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'

// jsdom keeps the document between tests; unmount so a query in one test can't
// match a component another test left mounted
afterEach(() => {
  cleanup()
})
