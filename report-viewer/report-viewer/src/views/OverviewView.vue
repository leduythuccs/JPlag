<!--
  A view displaying the overview file of a JPlag report.
-->
<template>
  <div class="grid grid-cols-1 grid-rows-[1fr_auto] gap-y-5 md:overflow-hidden">
    <ContainerComponent class="col-start-1 row-start-2">
      <div class="flex flex-col gap-x-5 md:flex-row md:items-center">
        <h2>JPlag Report</h2>
        <ToolTipComponent v-if="runInformation.failedSubmissions.length > 0" direction="bottom">
          <template #default>
            <p class="text-error font-bold">
              {{ runInformation.failedSubmissions.length }} invalid submissions. They are excluded
              from the comparison. Click "<i>More</i>" to show all failed submissions.
            </p>
          </template>
          <template #tooltip>
            <p class="max-w-[50rem] text-sm whitespace-pre-wrap">
              {{
                runInformation.failedSubmissions
                  .slice(0, 20)
                  .map((f) => f.submissionId)
                  .join(', ')
              }}<span v-if="runInformation.failedSubmissions.length > 20"
                >... (click "<i>More</i>" to see the complete list of failed submissions)</span
              >
            </p>
          </template>
        </ToolTipComponent>
      </div>

      <div
        class="flex flex-col gap-x-5 gap-y-2 md:flex-row md:items-center print:flex-col print:items-start"
      >
        <TextInformation label="Submission Directory" class="flex-auto">{{
          submissionPathValue
        }}</TextInformation>
        <TextInformation label="Result name" class="flex-auto">{{
          reportStore().getReportFileName()
        }}</TextInformation>
        <TextInformation label="Total Submissions" class="flex-auto">{{
          reportStore().getSubmissionCount()
        }}</TextInformation>

        <TextInformation label="Shown/Total Comparisons" class="flex-auto">
          <template #default
            >{{ reportStore().includedComparisonCount() }} /
            {{ runInformation.totalComparisons }}</template
          >
          <template #tooltip>
            <div class="text-sm whitespace-pre">
              <TextInformation label="Shown Comparisons">{{
                reportStore().includedComparisonCount()
              }}</TextInformation>
              <TextInformation label="Total Comparisons">{{
                runInformation.totalComparisons
              }}</TextInformation>
              <div v-if="missingComparisons > 0">
                <TextInformation label="Missing Comparisons">{{
                  missingComparisons
                }}</TextInformation>
                <p>
                  To include more comparisons in the report modify the number of shown comparisons
                  in the CLI.
                </p>
              </div>
            </div>
          </template>
        </TextInformation>

        <TextInformation label="Min Token Match" class="flex-auto">
          <template #default>
            {{ reportStore().getCliOptions().minimumTokenMatch }}
          </template>
          <template #tooltip>
            <div class="text-sm whitespace-pre">
              <p>
                Tunes the comparison sensitivity by adjusting the minimum token required to be
                counted as a matching section.
              </p>
              <p>It can be adjusted in the CLI.</p>
            </div>
          </template>
        </TextInformation>

        <ToolTipComponent direction="left" class="grow-0 print:hidden" :show-info-symbol="false">
          <template #default>
            <ButtonComponent @click="router.push({ name: 'InfoView' })"
              ><span class="flex items-center">More <InfoIcon /></span
            ></ButtonComponent>
          </template>
          <template #tooltip>
            <p class="text-sm whitespace-pre">More information about the CLI run of JPlag</p>
          </template>
        </ToolTipComponent>
      </div>
    </ContainerComponent>

    <ContainerComponent class="row-start-1 flex overflow-hidden print:hidden">
      <ComparisonTableWrapper
        :clusters="reportStore().getAllClusters()"
        :comparisons="topComparisons"
        class="min-h-0 max-w-full flex-1 print:min-h-full print:grow"
      >
        <template v-if="topComparisons.length < runInformation.totalComparisons" #footer>
          <p class="w-full pt-1 text-center font-bold">
            Not all comparisons are shown. To see more, re-run JPlag with a higher maximum number
            argument.
          </p>
        </template>
      </ComparisonTableWrapper>
    </ContainerComponent>
  </div>
</template>

<script setup lang="ts">
import { computed, onErrorCaptured } from 'vue'
import { redirectOnError, router } from '@/router'
import {
  ContainerComponent,
  ButtonComponent,
  TextInformation,
  ToolTipComponent,
  InfoIcon
} from '@jplag/ui-components/base'
import { reportStore } from '@/stores/reportStore'
import ComparisonTableWrapper from '../components/ComparisonTableWrapper.vue'

const runInformation = computed(() => reportStore().getRunInformation())
const topComparisons = computed(() => reportStore().getTopComparisons())

const hasMoreSubmissionPaths = computed(
  () => reportStore().getCliOptions().submissionDirectories.length > 1
)
const submissionPathValue = computed(() =>
  hasMoreSubmissionPaths.value
    ? 'Click More to see all paths'
    : reportStore().getCliOptions().submissionDirectories[0]
)

const missingComparisons = computed(
  () => runInformation.value.totalComparisons - reportStore().includedComparisonCount()
)

onErrorCaptured((error) => {
  redirectOnError(error, 'Error displaying overview:\n')
  return false
})
</script>
