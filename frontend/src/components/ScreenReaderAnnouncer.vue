<script setup>
import { announcer } from '../utils/announcer'
</script>

<template>
  <div class="sr-only" role="status" aria-live="polite" aria-atomic="true">
    {{ announcer.polite }}
  </div>
  <div class="sr-only" role="alert" aria-live="assertive" aria-atomic="true">
    {{ announcer.assertive }}
  </div>
</template>