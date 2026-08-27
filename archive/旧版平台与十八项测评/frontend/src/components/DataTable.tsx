interface Column<T> {
  key: string
  header: string
  render: (row: T) => React.ReactNode
}

interface DataTableProps<T> {
  columns: Column<T>[]
  data: T[]
  getRowKey: (row: T) => string
}

export function DataTable<T>({ columns, data, getRowKey }: DataTableProps<T>) {
  return (
    <table>
      <thead>
        <tr>
          {columns.map(col => <th key={col.key}>{col.header}</th>)}
        </tr>
      </thead>
      <tbody>
        {data.map(row => (
          <tr key={getRowKey(row)}>
            {columns.map(col => <td key={col.key}>{col.render(row)}</td>)}
          </tr>
        ))}
      </tbody>
    </table>
  )
}
