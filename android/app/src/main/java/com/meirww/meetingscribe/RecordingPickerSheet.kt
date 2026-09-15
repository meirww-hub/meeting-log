package com.meirww.meetingscribe

import android.content.Context
import android.view.LayoutInflater
import android.view.View
import android.view.WindowManager
import androidx.core.widget.addTextChangedListener
import androidx.recyclerview.widget.LinearLayoutManager
import com.google.android.material.bottomsheet.BottomSheetBehavior
import com.google.android.material.bottomsheet.BottomSheetDialog
import com.meirww.meetingscribe.databinding.SheetRecordingPickerBinding

/**
 * גיליון תחתון לבחירת ההקלטות שהצ'אט ישאל עליהן: חיפוש חופשי, רשימה נגללת
 * עם כל פרטי ההקלטה (תאריך, משך, סטטוס) וסימון מרובה.
 *
 * הבחירה מוחזרת גם בלחיצה על "אישור" וגם בסגירה רגילה של הגיליון, כדי שלא
 * ייווצר מצב שבו סימנו הקלטות והן נעלמו.
 */
class RecordingPickerSheet(
    private val context: Context,
    private val recordings: List<RecordingItem>,
    initialSelection: Set<String>,
    private val onDone: (Set<String>) -> Unit,
) {

    /** סוג השדה שלפיו מחפשים כרגע; כותרת היא ברירת המחדל. */
    private enum class FilterType { TITLE, SUMMARY }

    private val selected = initialSelection.toMutableSet()
    private var showSelectedOnly = false
    private var filterType = FilterType.TITLE

    private val binding = SheetRecordingPickerBinding.inflate(LayoutInflater.from(context))
    private val dialog = BottomSheetDialog(context)
    private val adapter = RecordingPickerAdapter(
        isSelected = { selected.contains(it.recordingId) },
        onToggle = { item ->
            if (!selected.remove(item.recordingId)) selected.add(item.recordingId)
            updateCounters()
            if (showSelectedOnly) applyFilter()
        },
    )

    @Suppress("DEPRECATION") // ADJUST_RESIZE עדיין הדרך לוודא שהמקלדת לא מכסה את שדה החיפוש
    fun show() {
        dialog.setContentView(binding.root)
        dialog.window?.setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE)
        // הרקע המעוגל שלנו (bg_sheet) מצויר על ה-root, אז מכבים את רקע ברירת
        // המחדל של הגיליון כדי שלא יציץ מאחורי הפינות.
        dialog.findViewById<View>(com.google.android.material.R.id.design_bottom_sheet)?.let { sheet ->
            sheet.setBackgroundResource(android.R.color.transparent)
            BottomSheetBehavior.from(sheet).apply {
                skipCollapsed = true
                state = BottomSheetBehavior.STATE_EXPANDED
            }
        }

        binding.pickerList.layoutManager = LinearLayoutManager(context)
        binding.pickerList.adapter = adapter

        binding.pickerSearchInput.addTextChangedListener { text ->
            binding.pickerClearSearch.visibility =
                if (text.isNullOrEmpty()) View.GONE else View.VISIBLE
            applyFilter()
        }
        binding.pickerClearSearch.setOnClickListener { binding.pickerSearchInput.setText("") }

        binding.pickerFilterTitle.setOnClickListener { setFilterType(FilterType.TITLE) }
        binding.pickerFilterSummary.setOnClickListener { setFilterType(FilterType.SUMMARY) }
        updateFilterTypeButtons()

        binding.pickerSelectAll.setOnClickListener {
            selected.addAll(visibleRecordings().map { it.recordingId })
            updateCounters()
            adapter.notifyDataSetChanged()
        }
        binding.pickerClearSelection.setOnClickListener {
            selected.clear()
            showSelectedOnly = false
            updateSelectedOnlyLabel()
            updateCounters()
            applyFilter()
        }
        binding.pickerSelectedOnly.setOnClickListener {
            showSelectedOnly = !showSelectedOnly
            updateSelectedOnlyLabel()
            applyFilter()
        }

        binding.pickerConfirmButton.setOnClickListener { dialog.dismiss() }
        dialog.setOnDismissListener { onDone(selected.toSet()) }

        updateSelectedOnlyLabel()
        updateCounters()
        applyFilter()
        dialog.show()
    }

    /** ההקלטות שמוצגות כרגע, אחרי חיפוש וסינון "רק הנבחרות". */
    private fun visibleRecordings(): List<RecordingItem> {
        val query = binding.pickerSearchInput.text?.toString()?.trim().orEmpty()
        return recordings
            .filter { item ->
                val matchesQuery = query.isBlank() || when (filterType) {
                    FilterType.TITLE ->
                        item.title.contains(query, ignoreCase = true) ||
                            item.note?.contains(query, ignoreCase = true) == true ||
                            item.date.contains(query) ||
                            item.date.toDisplayDate().contains(query)
                    FilterType.SUMMARY ->
                        item.summary?.contains(query, ignoreCase = true) == true
                }
                val matchesSelectedFilter = !showSelectedOnly || selected.contains(item.recordingId)
                matchesQuery && matchesSelectedFilter
            }
            .sortedByDescending { it.date }
    }

    /** מחליף את שדה החיפוש הפעיל ומרענן את הרשימה והתווית. */
    private fun setFilterType(type: FilterType) {
        if (filterType == type) return
        filterType = type
        updateFilterTypeButtons()
        applyFilter()
    }

    private fun updateFilterTypeButtons() {
        binding.pickerFilterTitle.isSelected = filterType == FilterType.TITLE
        binding.pickerFilterSummary.isSelected = filterType == FilterType.SUMMARY
        binding.pickerFilterTitle.setBackgroundResource(
            if (filterType == FilterType.TITLE) R.drawable.bg_chip_selected else R.drawable.bg_chip
        )
        binding.pickerFilterSummary.setBackgroundResource(
            if (filterType == FilterType.SUMMARY) R.drawable.bg_chip_selected else R.drawable.bg_chip
        )
        val selectedTextColor = android.graphics.Color.WHITE
        val unselectedTextColor = androidx.core.content.ContextCompat.getColor(context, R.color.accent_cyan)
        binding.pickerFilterTitle.setTextColor(
            if (filterType == FilterType.TITLE) selectedTextColor else unselectedTextColor
        )
        binding.pickerFilterSummary.setTextColor(
            if (filterType == FilterType.SUMMARY) selectedTextColor else unselectedTextColor
        )
        binding.pickerSearchInput.hint = context.getString(
            if (filterType == FilterType.SUMMARY) {
                R.string.chat_picker_search_hint_summary
            } else {
                R.string.chat_picker_search_hint
            }
        )
    }

    private fun applyFilter() {
        val visible = visibleRecordings()
        adapter.submitList(visible)
        binding.pickerEmptyText.visibility = if (visible.isEmpty()) View.VISIBLE else View.GONE
        binding.pickerList.visibility = if (visible.isEmpty()) View.GONE else View.VISIBLE
    }

    private fun updateSelectedOnlyLabel() {
        binding.pickerSelectedOnly.setText(
            if (showSelectedOnly) R.string.chat_picker_show_all else R.string.chat_picker_selected_only
        )
    }

    private fun updateCounters() {
        binding.pickerSelectedCount.text =
            context.getString(R.string.chat_picker_selected_count, selected.size)
        binding.pickerConfirmButton.text = if (selected.isEmpty()) {
            context.getString(R.string.chat_picker_confirm_empty)
        } else {
            context.getString(R.string.chat_picker_confirm, selected.size)
        }
    }
}
