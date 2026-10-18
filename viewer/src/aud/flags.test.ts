import { expect, test } from 'bun:test'
import { country, flagOf } from './flags'

test('country from the name', () => {
  expect(country('佐藤')).toBe('JP')
  expect(country('Kim Minsu')).toBe('KR')
  expect(country('Yu Tanaka')).toBe('')
  expect(country('Yu Tanaka', { 'Yu Tanaka': 'JP' })).toBe('JP')
})
test('flagOf', () => { expect(flagOf('JP')).toBe('🇯🇵'); expect(flagOf('')).toBe(''); expect(flagOf('jp')).toBe('') })
