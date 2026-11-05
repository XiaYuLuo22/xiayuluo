/** Presentation only. Collapsing details must not cancel work or dispose children. */
export function settingsDisclosure(expanded: boolean) {
  return {
    display: expanded ? 'flex' as const : 'none' as const,
    accessibilityElementsHidden: !expanded,
    importantForAccessibility: expanded ? 'auto' as const : 'no-hide-descendants' as const,
  };
}
