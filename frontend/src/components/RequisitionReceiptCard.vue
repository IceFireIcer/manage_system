<script setup>
import { computed } from 'vue'

const props = defineProps({
  receipts: { type: Array, default: () => [] },
  limit: { type: Number, default: 3 },
})

defineEmits(['clear'])

const visible = computed(() => props.receipts.slice(0, props.limit))
</script>

<template>
  <section class="receipt-card panel" aria-labelledby="requisition-recent-receipt-title">
    <div class="receipt-card__head">
      <h2 id="requisition-recent-receipt-title" class="receipt-card__title">最近回执</h2>
      <button type="button" class="receipt-card__clear" @click="$emit('clear')">清除</button>
    </div>
    <ul class="receipt-card__list">
      <li
        v-for="receipt in visible"
        :key="receipt.orderNo || receipt.createdAt"
        class="receipt-card__item"
      >
        <div class="receipt-card__top">
          <span class="receipt-card__order">{{ receipt.orderNo || '本机回执' }}</span>
          <span v-if="receipt.businessAt" class="receipt-card__time">{{ receipt.businessAt }}</span>
        </div>
        <p v-if="receipt.ownerName" class="receipt-card__owner">领用人 {{ receipt.ownerName }}</p>
        <ul class="receipt-card__lines">
          <li
            v-for="(line, index) in receipt.lines"
            :key="`${line.name}-${index}`"
            class="receipt-card__line"
          >
            <span class="receipt-card__line-name">{{ line.name }}</span>
            <span class="receipt-card__line-qty">
              {{ line.quantity }}{{ line.unit ? ` ${line.unit}` : '' }}
            </span>
          </li>
        </ul>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.receipt-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
}

.receipt-card__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}

.receipt-card__title {
  margin: 0;
  font-size: 15px;
  line-height: 22px;
  font-weight: 600;
  color: var(--text);
}

.receipt-card__clear {
  min-height: 32px;
  padding: 4px 10px;
  border: 1px solid transparent;
  border-radius: var(--radius-control);
  background: transparent;
  color: var(--text-muted);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}

.receipt-card__clear:hover {
  border-color: var(--line-soft);
  color: var(--text);
}

.receipt-card__list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.receipt-card__item {
  padding: var(--space-3);
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-control);
  background: var(--surface-subtle);
}

.receipt-card__top {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: 4px 12px;
}

.receipt-card__order {
  font-size: 14px;
  line-height: 22px;
  font-weight: 600;
  color: var(--text);
  font-variant-numeric: tabular-nums;
  word-break: break-all;
}

.receipt-card__time {
  font-size: 13px;
  line-height: 20px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.receipt-card__owner {
  margin: 2px 0 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--text-secondary);
}

.receipt-card__lines {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin: var(--space-2) 0 0;
  padding: 0;
  list-style: none;
}

.receipt-card__line {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
  font-size: 13px;
  line-height: 20px;
}

.receipt-card__line-name {
  min-width: 0;
  color: var(--text);
}

.receipt-card__line-qty {
  flex: 0 0 auto;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}

@media (max-width: 480px) {
  .receipt-card__clear {
    min-height: 44px;
    min-width: 44px;
    padding: 4px 12px;
  }
}
</style>