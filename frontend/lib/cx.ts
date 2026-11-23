export type Tone = 'neutral' | 'brand' | 'success' | 'warning' | 'danger';

export function cx(...classes: Array<string | false | null | undefined>) {
  return classes.filter(Boolean).join(' ');
}
