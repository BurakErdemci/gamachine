import { useEffect } from 'react'
import { useRouter } from 'next/router'

// Only the relay-served export needs a root page (the relay falls back to /app/index.html); the
// desktop window loads app://./home directly and never renders this.
export default function Index() {
  const router = useRouter()
  useEffect(() => {
    router.replace('/home/')
  }, [router])
  return null
}
