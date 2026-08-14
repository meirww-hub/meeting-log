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
 * עם כל פרטי ההקלטה (תאריך, דוברים, משך, סטטוס) וסימון מרובה.
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

    private val selected = initialSelection.toMutableSet()
    private var showSelectedOnly = false

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
                val matchesQuery = query.isBlank() ||
                    item.title.contains(query, ignoreCase = true) ||
                    item.speakers.any { it.contains(query, ignoreCase = true) } ||
                    item.note?.contains(query, ignoreCase = true) == true ||
                    item.date.contains(query) ||
                    item.date.toDisplayDate().contains(query)
                val matchesSelectedFilter = !showSelectedOnly || selected.contains(item.recordingId)
                matchesQuery && matchesSelectedFilter
            }
            .sortedByDescending { it.date }
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
