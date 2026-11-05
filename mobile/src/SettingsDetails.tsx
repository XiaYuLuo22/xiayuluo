import { useState, type ReactNode } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { settingsDisclosure } from './settingsDisclosure';

/** Keep children mounted: hiding a settings panel never stops its controller. */
export function SettingsDetails({ title, children }: { title: string; children: ReactNode }) {
  const [expanded, setExpanded] = useState(false);
  const visibility = settingsDisclosure(expanded);
  return <View style={s.details}>
    <Pressable accessibilityRole="button" accessibilityLabel={title}
      accessibilityState={{ expanded }} accessibilityHint={expanded ? '收起详细设置' : '展开详细设置'}
      onPress={() => setExpanded(current => !current)} style={s.toggle}>
      <Text style={s.title}>{title}</Text><Text accessible={false} style={s.marker}>{expanded ? '−' : '＋'}</Text>
    </Pressable>
    <View style={{ display: visibility.display }} accessibilityElementsHidden={visibility.accessibilityElementsHidden}
      importantForAccessibility={visibility.importantForAccessibility}>
      {children}
    </View>
  </View>;
}

const s = StyleSheet.create({
  details: { marginTop: 8 },
  toggle: { minHeight: 44, paddingVertical: 10, flexDirection: 'row', alignItems: 'center', gap: 12 },
  title: { color: '#607b55', fontSize: 12, lineHeight: 20, flex: 1 },
  marker: { color: '#729568', fontSize: 18 },
});
